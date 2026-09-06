#!/bin/bash
# Deploys the ClearCut ADK agent to Vertex AI Agent Engine.
# Requires: .env at repo root (see .env.example), gcloud auth, APIs enabled (see setup.sh).
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a

: "${GOOGLE_CLOUD_PROJECT:?set in .env}"
: "${GOOGLE_CLOUD_LOCATION:?set in .env}"
: "${MODEL:?set MODEL in .env to the current Gemini model ID from Vertex Model Garden}"
: "${PARALLEL_API_KEY:?set in .env}"

# The runtime only needs these; keep the deploy env minimal.
ENV_TMP="$(mktemp)"
trap 'rm -f "$ENV_TMP"' EXIT
cat > "$ENV_TMP" <<ENV
GOOGLE_GENAI_USE_VERTEXAI=1
MODEL=$MODEL
MODEL_LOCATION=${MODEL_LOCATION:-global}
PARALLEL_API_KEY=$PARALLEL_API_KEY
CLEARCUT_MAX_ENTITIES=${CLEARCUT_MAX_ENTITIES:-120}
CLEARCUT_MAX_RESULTS=${CLEARCUT_MAX_RESULTS:-5}
CLEARCUT_MAX_WORKERS=${CLEARCUT_MAX_WORKERS:-8}
CLEARCUT_SEARCH_MODE=${CLEARCUT_SEARCH_MODE:-fast}
ENV

EXTRA=()
if [[ -n "${AGENT_ENGINE_ID:-}" ]]; then
  EXTRA+=(--agent_engine_id="$AGENT_ENGINE_ID")   # update in place
fi

adk deploy agent_engine \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --region="$GOOGLE_CLOUD_LOCATION" \
  --display_name="ClearCut Script Clearance Agent" \
  --description="Script clearance: extract -> research (Parallel Search) -> adjudicate -> report" \
  --env_file="$ENV_TMP" \
  --requirements_file=clearcut_agent/requirements.txt \
  --trace_to_cloud \
  "${EXTRA[@]}" \
  clearcut_agent

echo
echo "Copy the reasoningEngines ID from the output above into AGENT_ENGINE_ID in .env, then run ./deploy_web.sh"
