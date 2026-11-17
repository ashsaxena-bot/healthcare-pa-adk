# Healthcare Payor Prior Authorization (PA) — Google ADK

Production-grade **Google Agent Development Kit (ADK)** multi-agent system automating clinical intake, CMS Local Coverage Determination (`LCD L34220`) verification, PHI/PII redaction, and Human-in-the-Loop (HITL) Medical Director gating for Prior Authorization requests.

## Architecture & Directory Structure

- `pa_agent/` — ADK entrypoint package exposing `root_agent` (`pa_coordinator_agent`).
- `agents/` — Clinical & regulatory Constitutions (`prompts.py`) and ADK multi-agent orchestration (`ParallelAgent` -> `SequentialAgent` in `coordinator.py`).
- `tools/` — Strict Pydantic-validated FHIR R4 timeline parser (`fhir_parser.py`) and CMS LCD policy / HITL adverse determination tools (`pa_tools.py`).
- `middleware/` — Firestore/SQLite persistent session state & sliding-window history compaction (`memory.py`), `structlog` + OpenTelemetry + Cloud DLP / regex PHI scrubber (`observability.py`), and Secret Manager config (`secrets.py`).
- `data/` — Synthea FHIR R4 bundles (`patient_case_001.json`, `patient_case_002.json`) and CMS policy `cms_lcd_lumbar_mri.txt` (`LCD L34220`).
- `evals/` — 3-case golden evaluation dataset (`golden_dataset.json`) and automated `pytest` verification suite (`test_pa_eval.py`).
- `infra/` — Terraform IaC (`main.tf`) and automated `adk deploy` script (`deploy.sh`).

## Setup & Installation

> **Note:** Full `google-adk` CLI (`adk web`, `adk eval`, `adk deploy`) requires **Python 3.10+**. Local offline evaluation (`pytest`) also supports Python 3.9+ using prebuilt wheels (`--only-binary=:all:`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --only-binary=:all: -r requirements.txt
```

## Agent CLI Workflows

### 1. Local Interactive Web UI (`adk web`)
Launch the ADK developer UI against `pa_agent/agent.py`:
```bash
adk web .
```

### 2. Automated Evaluation Suite (`pytest` & `adk eval`)
Run deterministic unit, trajectory, guardrail, PII redaction, and HITL tests:
```bash
.venv/bin/pytest -v evals/test_pa_eval.py
```
When running with `google-adk` (Python 3.10+) and `GEMINI_API_KEY` configured:
```bash
adk eval pa_agent evals/golden_dataset.json
```

### 3. Production Deployment (`adk deploy`)
Provision Secret Manager, Firestore, IAM, and Vertex AI APIs via Terraform and deploy to Vertex AI Agent Engine:
```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export STAGING_BUCKET="gs://your-adk-staging-bucket"
./infra/deploy.sh
```
