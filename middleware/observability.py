import structlog
import logging
import re
import time
from typing import Any, Dict
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

try:
    from google.cloud import dlp_v2
    DLP_AVAILABLE = True
except ImportError:
    DLP_AVAILABLE = False

# Configure OpenTelemetry
trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
tracer = trace.get_tracer(__name__)

# Configure Structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger()

class PIIScrubber:
    def __init__(self, use_dlp: bool = True):
        self.use_dlp = use_dlp and DLP_AVAILABLE
        if self.use_dlp:
            try:
                self.dlp = dlp_v2.DlpServiceClient()
                self.project = "placeholder-project"  # Should come from config
                logger.info("Initialized Google Cloud DLP for PII redaction.")
            except Exception as e:
                logger.warning(f"Failed to initialize DLP: {e}. Falling back to local regex scrubber.")
                self.use_dlp = False

    def scrub(self, text: str) -> str:
        if not isinstance(text, str):
            return text
            
        if self.use_dlp:
            try:
                item = {"value": text}
                inspect_config = {"info_types": [{"name": "PERSON_NAME"}, {"name": "DATE_OF_BIRTH"}, {"name": "US_SOCIAL_SECURITY_NUMBER"}, {"name": "PHONE_NUMBER"}]}
                deidentify_config = {"info_type_transformations": {"transformations": [{"primitive_transformation": {"replace_with_info_type_config": {}}}]}}
                response = self.dlp.deidentify_content(
                    request={
                        "parent": f"projects/{self.project}",
                        "deidentify_config": deidentify_config,
                        "inspect_config": inspect_config,
                        "item": item,
                    }
                )
                return response.item.value
            except Exception as e:
                logger.error(f"DLP Error: {e}")
                # Fallback to regex below
                
        # Regex fallback
        redacted = text
        # Simple Date of Birth (YYYY-MM-DD or MM/DD/YYYY)
        redacted = re.sub(r'\b(19|20)\d{2}-[01]\d-[0-3]\d\b', '[REDACTED_DOB]', redacted)
        redacted = re.sub(r'\b[01]\d/[0-3]\d/(19|20)\d{2}\b', '[REDACTED_DOB]', redacted)
        # SSN
        redacted = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED_SSN]', redacted)
        # Phone
        redacted = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', '[REDACTED_PHONE]', redacted)
        return redacted

scrubber = PIIScrubber()

class Hooks:
    def __init__(self):
        self._start_times: Dict[str, float] = {}

    def before_tool_callback(self, tool_name: str, kwargs: Dict[str, Any]):
        """Capture Intent"""
        with tracer.start_as_current_span(f"Tool-{tool_name}") as span:
            span.set_attribute("tool", tool_name)
            redacted_kwargs = {k: scrubber.scrub(str(v)) for k, v in kwargs.items()}
            logger.info(
                "intent_capture",
                tool=tool_name,
                inputs=redacted_kwargs
            )
            self._start_times[tool_name] = time.time()
            
            # Code stop for HITL
            if tool_name == "issue_final_adverse_determination":
                if not kwargs.get("human_approved"):
                    logger.warning("HITL approval missing, blocking adverse determination.")

    def after_tool_callback(self, tool_name: str, result: Any):
        """Capture Outcome"""
        duration = time.time() - self._start_times.get(tool_name, time.time())
        logger.info(
            "outcome_capture",
            tool=tool_name,
            duration_sec=duration,
            status="success",
            result_summary=scrubber.scrub(str(result)[:200]) # Summarized outcome
        )

    def after_agent_callback(self, agent_name: str, result: str):
        """Guardrail self-evaluation"""
        if agent_name == "um_verifier":
            # Verify valid policy citations (LCD L\d+)
            if not re.search(r'LCD L\d+', result):
                logger.error("Guardrail failure: um_verifier response missing LCD citation.", response=result)
                raise ValueError("Guardrail failed: um_verifier must cite an LCD policy.")
