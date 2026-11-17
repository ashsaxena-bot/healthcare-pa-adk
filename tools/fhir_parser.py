import json
import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime

class TherapyRecord(BaseModel):
    """Represents a documented therapy (physical therapy, medication) in the patient's record."""
    therapy_type: str = Field(description="Type of therapy (e.g., Physical Therapy, NSAID).")
    start_date: str = Field(description="Start date of the therapy in ISO format (YYYY-MM-DD).")
    end_date: Optional[str] = Field(None, description="End date of the therapy if completed.")
    duration_days: Optional[int] = Field(None, description="Duration of therapy in days.")
    status: str = Field(description="Status of the therapy (e.g., completed, active).")

class ClinicalTimelineOutput(BaseModel):
    """Extracted clinical timeline of the patient for Prior Authorization."""
    patient_id: str = Field(description="Patient identifier.")
    conditions: List[str] = Field(default_factory=list, description="List of condition ICD-10 codes or descriptions.")
    therapies: List[TherapyRecord] = Field(default_factory=list, description="List of conservative therapies attempted.")
    red_flags: List[str] = Field(default_factory=list, description="Identified red flag symptoms.")
    requested_service: Optional[str] = Field(None, description="CPT code or description of the requested service.")
    error: Optional[str] = Field(None, description="Error message if extraction fails.")
    guidance_message: Optional[str] = Field(None, description="Guidance to the LLM on how to recover from error.")

def extract_fhir_clinical_timeline(fhir_file_path: str) -> dict:
    """
    Extracts a clinical timeline for Prior Authorization from a FHIR R4 Bundle.

    Args:
        fhir_file_path (str): The path to the FHIR JSON file.

    Returns:
        dict: A dictionary representation of ClinicalTimelineOutput.
    """
    if not os.path.exists(fhir_file_path):
        return ClinicalTimelineOutput(
            patient_id="unknown",
            error="FileNotFoundError",
            guidance_message=f"The FHIR file at {fhir_file_path} was not found. Please verify the path and try again."
        ).model_dump()
    
    try:
        with open(fhir_file_path, 'r') as f:
            data = json.load(f)
            
        patient_id = "unknown"
        conditions = []
        therapies = []
        red_flags = []
        requested_service = None
        
        for entry in data.get("entry", []):
            resource = entry.get("resource", {})
            rtype = resource.get("resourceType")
            
            if rtype == "Patient":
                patient_id = resource.get("id", patient_id)
            elif rtype == "Condition":
                for coding in resource.get("code", {}).get("coding", []):
                    conditions.append(coding.get("code") or coding.get("display"))
            elif rtype == "Procedure":
                code = resource.get("code", {}).get("coding", [{}])[0].get("code")
                start = resource.get("performedPeriod", {}).get("start")
                end = resource.get("performedPeriod", {}).get("end")
                status = resource.get("status", "unknown")
                duration = None
                if start and end:
                    try:
                        start_dt = datetime.fromisoformat(start)
                        end_dt = datetime.fromisoformat(end)
                        duration = (end_dt - start_dt).days
                    except ValueError:
                        pass
                therapies.append(TherapyRecord(
                    therapy_type=f"Procedure {code}",
                    start_date=start or "",
                    end_date=end,
                    duration_days=duration,
                    status=status
                ))
            elif rtype == "MedicationRequest":
                med_text = resource.get("medicationCodeableConcept", {}).get("text", "Unknown Med")
                authored_on = resource.get("authoredOn", "")
                status = resource.get("status", "unknown")
                therapies.append(TherapyRecord(
                    therapy_type=med_text,
                    start_date=authored_on,
                    status=status
                ))
            elif rtype == "ServiceRequest":
                for coding in resource.get("code", {}).get("coding", []):
                    requested_service = coding.get("code")
                notes = resource.get("note", [])
                red_flag_terms = (
                    "cauda equina",
                    "saddle anesthesia",
                    "bowel/bladder dysfunction",
                    "bowel dysfunction",
                    "bladder dysfunction",
                    "focal neurological deficit",
                    "severe motor weakness",
                    "progressive weakness",
                    "malignancy",
                    "epidural abscess",
                    "vertebral fracture",
                )
                negation_prefixes = ("no ", "without ", "denies ", "negative for ", "normal ")
                for note in notes:
                    raw_text = note.get("text", "")
                    for sentence in [s.strip() for s in raw_text.split(".") if s.strip()]:
                        lowered = sentence.lower()
                        if any(lowered.startswith(neg) or f" {neg}" in lowered for neg in negation_prefixes):
                            continue
                        if any(term in lowered for term in red_flag_terms):
                            red_flags.append(sentence)
                        
        output = ClinicalTimelineOutput(
            patient_id=patient_id,
            conditions=[c for c in conditions if c],
            therapies=therapies,
            red_flags=red_flags,
            requested_service=requested_service
        )
        return output.model_dump()
        
    except json.JSONDecodeError:
        return ClinicalTimelineOutput(
            patient_id="unknown",
            error="JSONDecodeError",
            guidance_message="The FHIR file is not valid JSON. Please provide a correctly formatted FHIR R4 Bundle."
        ).model_dump()
    except Exception as e:
        return ClinicalTimelineOutput(
            patient_id="unknown",
            error=str(e),
            guidance_message="An unexpected error occurred during extraction. Check the file structure."
        ).model_dump()
