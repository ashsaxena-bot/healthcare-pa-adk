#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?ERROR: PROJECT_ID environment variable is required}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:?ERROR: STAGING_BUCKET environment variable is required (e.g. gs://my-pa-staging)}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

terraform -chdir="${REPO_ROOT}/infra" init -input=false
terraform -chdir="${REPO_ROOT}/infra" apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --staging_bucket="${STAGING_BUCKET}" \
  --display_name="healthcare-pa-adk" \
  "${REPO_ROOT}/pa_agent"
