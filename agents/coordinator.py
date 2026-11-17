from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from agents.prompts import (
    CLINICAL_EXTRACTOR_PROMPT,
    POLICY_RETRIEVER_PROMPT,
    UM_VERIFIER_PROMPT,
)
from middleware.observability import Hooks
from tools.fhir_parser import extract_fhir_clinical_timeline
from tools.pa_tools import fetch_cms_lcd_policy_by_cpt, issue_final_adverse_determination

hooks = Hooks()

try:
    from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent  # type: ignore

    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False

    class LlmAgent:  # type: ignore[no-redef]
        """Lightweight offline ADK-compatible LlmAgent fallback for Python <3.10 test environments."""

        def __init__(
            self,
            name: str,
            model: str,
            instruction: str,
            description: str = "",
            tools: Optional[List[Callable[..., Any]]] = None,
            output_key: Optional[str] = None,
            before_tool_callback: Optional[Callable[..., Any]] = None,
            after_tool_callback: Optional[Callable[..., Any]] = None,
            after_agent_callback: Optional[Callable[..., Any]] = None,
        ) -> None:
            self.name = name
            self.model = model
            self.instruction = instruction
            self.description = description
            self.tools = tools or []
            self.output_key = output_key
            self.before_tool_callback = before_tool_callback
            self.after_tool_callback = after_tool_callback
            self.after_agent_callback = after_agent_callback

    class ParallelAgent:  # type: ignore[no-redef]
        """Lightweight offline ADK-compatible ParallelAgent fallback."""

        def __init__(self, name: str, sub_agents: List[Any], description: str = "") -> None:
            self.name = name
            self.sub_agents = sub_agents
            self.description = description

    class SequentialAgent:  # type: ignore[no-redef]
        """Lightweight offline ADK-compatible SequentialAgent fallback."""

        def __init__(self, name: str, sub_agents: List[Any], description: str = "") -> None:
            self.name = name
            self.sub_agents = sub_agents
            self.description = description


def _adk_before_tool(tool: Any, args: Dict[str, Any], tool_context: Any = None) -> Optional[Dict[str, Any]]:
    """ADK before_tool_callback adapter enforcing intent logging and HITL gate."""
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
    hooks.before_tool_callback(tool_name, args)
    if tool_name == "issue_final_adverse_determination":
        if not args.get("human_approved") or not args.get("reviewer_id"):
            return issue_final_adverse_determination(
                reason=args.get("reason", "Criteria not met"),
                human_approved=False,
                reviewer_id="",
            )
    return None


def _adk_after_tool(tool: Any, args: Dict[str, Any], tool_context: Any = None, tool_response: Any = None) -> None:
    """ADK after_tool_callback adapter capturing structured tool outcomes."""
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
    hooks.after_tool_callback(tool_name, tool_response)


def _adk_after_um_agent(callback_context: Any) -> None:
    """ADK after_agent_callback guardrail verifying CMS LCD policy citation."""
    text = getattr(callback_context, "output_text", None)
    if isinstance(text, str) and text:
        hooks.after_agent_callback("um_verifier", text)


clinical_extractor = LlmAgent(
    name="clinical_extractor",
    model="gemini-2.5-flash",
    description="Extracts structured clinical timelines and conservative therapy durations from FHIR R4 bundles.",
    instruction=CLINICAL_EXTRACTOR_PROMPT,
    tools=[extract_fhir_clinical_timeline],
    output_key="clinical_timeline",
    before_tool_callback=_adk_before_tool,
    after_tool_callback=_adk_after_tool,
)

policy_retriever = LlmAgent(
    name="policy_retriever",
    model="gemini-2.5-flash",
    description="Retrieves applicable CMS LCD coverage criteria by CPT code.",
    instruction=POLICY_RETRIEVER_PROMPT,
    tools=[fetch_cms_lcd_policy_by_cpt],
    output_key="cms_policy",
    before_tool_callback=_adk_before_tool,
    after_tool_callback=_adk_after_tool,
)

parallel_intake_agent = ParallelAgent(
    name="parallel_intake_agent",
    description="Concurrently extracts patient clinical timeline and retrieves CMS LCD policy.",
    sub_agents=[clinical_extractor, policy_retriever],
)

um_verifier = LlmAgent(
    name="um_verifier",
    model="gemini-2.5-pro",
    description="Verifies medical necessity against CMS LCD criteria and enforces HITL on adverse determinations.",
    instruction=UM_VERIFIER_PROMPT,
    tools=[issue_final_adverse_determination],
    output_key="um_determination",
    before_tool_callback=_adk_before_tool,
    after_tool_callback=_adk_after_tool,
    after_agent_callback=_adk_after_um_agent,
)

pa_coordinator_agent = SequentialAgent(
    name="pa_coordinator_agent",
    description="End-to-end Prior Authorization coordinator running parallel intake followed by UM verification.",
    sub_agents=[parallel_intake_agent, um_verifier],
)


async def execute_pa_workflow(
    fhir_file_path: str,
    requested_cpt: str,
    human_approved: bool = False,
    reviewer_id: str = "",
) -> Dict[str, Any]:
    """Executes the Prior Authorization trajectory deterministically for local evaluation and CLI usage."""

    async def _run_extractor() -> Dict[str, Any]:
        hooks.before_tool_callback("extract_fhir_clinical_timeline", {"fhir_file_path": fhir_file_path})
        res = await asyncio.to_thread(extract_fhir_clinical_timeline, fhir_file_path)
        hooks.after_tool_callback("extract_fhir_clinical_timeline", res)
        return res

    async def _run_policy() -> Dict[str, Any]:
        hooks.before_tool_callback("fetch_cms_lcd_policy_by_cpt", {"cpt_code": requested_cpt})
        res = await asyncio.to_thread(fetch_cms_lcd_policy_by_cpt, requested_cpt)
        hooks.after_tool_callback("fetch_cms_lcd_policy_by_cpt", res)
        return res

    timeline, policy = await asyncio.gather(_run_extractor(), _run_policy())

    if timeline.get("error") or policy.get("error"):
        guidance = policy.get("guidance_message") or timeline.get("guidance_message")
        return {
            "status": "ERROR_RECOVERY",
            "timeline": timeline,
            "policy": policy,
            "guidance_message": guidance,
            "trajectory": ["extract_fhir_clinical_timeline", "fetch_cms_lcd_policy_by_cpt"],
        }

    policy_id = policy["policy_id"]
    max_pt_days = max(
        (t.get("duration_days") or 0 for t in timeline.get("therapies", [])),
        default=0,
    )
    has_red_flags = bool(timeline.get("red_flags"))

    if has_red_flags or max_pt_days >= 42:
        rationale = (
            f"APPROVED per {policy_id}: Documented conservative therapy of {max_pt_days} days "
            f"(>=42 days required) or Red Flag exception satisfied."
        )
        hooks.after_agent_callback("um_verifier", rationale)
        return {
            "status": "APPROVED",
            "policy_citation": policy_id,
            "rationale": rationale,
            "trajectory": ["extract_fhir_clinical_timeline", "fetch_cms_lcd_policy_by_cpt"],
        }

    denial_reason = (
        f"Does not meet {policy_id} medical necessity criteria: only {max_pt_days} days of "
        f"conservative management documented (<42 days required) and no Red Flag exceptions present."
    )
    det_args = {
        "reason": denial_reason,
        "human_approved": human_approved,
        "reviewer_id": reviewer_id,
    }
    blocked = _adk_before_tool(issue_final_adverse_determination, det_args)
    determination = blocked or issue_final_adverse_determination(**det_args)
    hooks.after_tool_callback("issue_final_adverse_determination", determination)
    hooks.after_agent_callback("um_verifier", denial_reason)

    return {
        "status": determination["status"],
        "policy_citation": policy_id,
        "rationale": denial_reason,
        "determination": determination,
        "trajectory": [
            "extract_fhir_clinical_timeline",
            "fetch_cms_lcd_policy_by_cpt",
            "issue_final_adverse_determination",
        ],
    }
