import asyncio
import json
import os
import pytest

from agents.coordinator import execute_pa_workflow, hooks
from middleware.memory import MemoryManager
from middleware.observability import PIIScrubber
from pa_agent.agent import root_agent
from tools.fhir_parser import extract_fhir_clinical_timeline
from tools.pa_tools import fetch_cms_lcd_policy_by_cpt, issue_final_adverse_determination

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_extract_fhir_clinical_timeline():
    path1 = os.path.join(BASE_DIR, "data", "patient_case_001.json")
    result1 = extract_fhir_clinical_timeline(path1)
    assert result1["patient_id"] == "patient-001"
    assert result1["therapies"][0]["duration_days"] == 14
    assert result1["red_flags"] == []

    path2 = os.path.join(BASE_DIR, "data", "patient_case_002.json")
    result2 = extract_fhir_clinical_timeline(path2)
    assert result2["patient_id"] == "patient-002"
    assert result2["therapies"][0]["duration_days"] == 47
    assert result2["red_flags"] == []


def test_fetch_cms_lcd_policy():
    res = fetch_cms_lcd_policy_by_cpt("72148")
    assert res["policy_id"] == "LCD L34220"

    res2 = fetch_cms_lcd_policy_by_cpt("99999")
    assert res2["error"] == "InvalidCPTCode"
    assert res2["guidance_message"] is not None


def test_issue_adverse_determination_hitl():
    res = issue_final_adverse_determination("Does not meet 6 week PT", human_approved=False)
    assert res["status"] == "PENDING_HITL"

    res2 = issue_final_adverse_determination(
        "Does not meet 6 week PT",
        human_approved=True,
        reviewer_id="MD-1234",
    )
    assert res2["status"] == "DENIED"


def test_pii_scrubber():
    scrubber = PIIScrubber(use_dlp=False)
    scrubbed = scrubber.scrub("DOB: 1980-05-20, SSN: 123-45-6789, Phone: 555-123-4567")
    assert "[REDACTED_DOB]" in scrubbed
    assert "[REDACTED_SSN]" in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed


def test_golden_dataset_trajectories_and_guardrails():
    assert root_agent.name == "pa_coordinator_agent"

    golden_path = os.path.join(BASE_DIR, "evals", "golden_dataset.json")
    with open(golden_path, "r") as f:
        golden_cases = json.load(f)

    assert len(golden_cases) == 3

    for case in golden_cases:
        fhir_path = os.path.join(BASE_DIR, case["fhir_path"])
        outcome = asyncio.run(execute_pa_workflow(fhir_path, case["cpt"]))
        assert outcome["status"] == case["expected_status"]
        if case["expected_status"] in ("APPROVED", "PENDING_HITL"):
            assert outcome["policy_citation"] == "LCD L34220"
            assert "LCD L34220" in outcome["rationale"]
        if case["expected_status"] == "PENDING_HITL":
            assert "issue_final_adverse_determination" in outcome["trajectory"]
        if case["expected_status"] == "ERROR_RECOVERY":
            assert outcome["guidance_message"] is not None

    with pytest.raises(ValueError, match="must cite an LCD policy"):
        hooks.after_agent_callback("um_verifier", "Service approved without policy reference.")


def test_memory_compaction_and_sqlite_persistence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mem = MemoryManager(use_firestore=False)
    history = [{"role": "system", "content": "Constitution"}] + [
        {"role": "user", "content": f"Turn {i}: " + ("clinical note " * 40)}
        for i in range(25)
    ]
    compacted = mem.compact_history(history, max_tokens=300)
    assert len(compacted) < len(history)
    assert compacted[1]["content"] == "[EARLIER HISTORY COMPACTED]"

    asyncio.run(mem.save_history("sess-001", compacted))
    assert os.path.exists(tmp_path / "pa_sessions.db")
