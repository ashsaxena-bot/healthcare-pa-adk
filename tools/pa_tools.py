from __future__ import annotations

import os
from typing import Optional
from pydantic import BaseModel, Field

class PolicyResult(BaseModel):
    policy_id: str = Field(description="Policy ID (e.g., LCD L34220).")
    content: str = Field(description="Policy content.")
    error: Optional[str] = Field(None, description="Error message if policy fetch fails.")
    guidance_message: Optional[str] = Field(None, description="Guidance to the LLM on how to recover.")

def fetch_cms_lcd_policy_by_cpt(cpt_code: str) -> dict:
    """
    Fetches the CMS LCD policy for a given CPT code.

    Args:
        cpt_code (str): The CPT code to look up (e.g., '72148').

    Returns:
        dict: A dictionary representation of PolicyResult containing the policy or error guidance.
    """
    valid_cpts = ["72148", "72149", "72158"]
    if cpt_code not in valid_cpts:
        return PolicyResult(
            policy_id="unknown",
            content="",
            error="InvalidCPTCode",
            guidance_message=f"No CMS LCD policy found for CPT {cpt_code}. Please verify the code or request manual review."
        ).model_dump()
        
    policy_path = os.path.join(os.path.dirname(__file__), "..", "data", "policies", "cms_lcd_lumbar_mri.txt")
    if not os.path.exists(policy_path):
        return PolicyResult(
            policy_id="unknown",
            content="",
            error="FileNotFoundError",
            guidance_message="The policy file could not be found. Please alert the system administrator."
        ).model_dump()
        
    with open(policy_path, "r") as f:
        content = f.read()
        
    return PolicyResult(
        policy_id="LCD L34220",
        content=content
    ).model_dump()

class AdverseDeterminationResult(BaseModel):
    status: str = Field(description="Status of the determination (e.g., 'DENIED', 'PENDING_HITL').")
    reason: str = Field(description="Reason for denial.")
    guidance_message: Optional[str] = Field(None, description="Instructions if HITL approval is missing.")

def issue_final_adverse_determination(reason: str, human_approved: bool = False, reviewer_id: str = "") -> dict:
    """
    Issues a final adverse determination (denial) for a prior authorization request.
    Requires explicit human approval.

    Args:
        reason (str): The clinical reason for denial.
        human_approved (bool): Must be True to proceed with the denial.
        reviewer_id (str): The ID of the Medical Director approving the denial.

    Returns:
        dict: A dictionary representation of AdverseDeterminationResult.
    """
    if not human_approved or not reviewer_id:
        return AdverseDeterminationResult(
            status="PENDING_HITL",
            reason=reason,
            guidance_message="Human Medical Director approval is required. Please wait for HITL confirmation before finalizing the denial."
        ).model_dump()
        
    return AdverseDeterminationResult(
        status="DENIED",
        reason=reason
    ).model_dump()
