#!/bin/bash
# Deploys the ClearCut web UI to Cloud Run (builds from the Dockerfile at repo root).
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?}"; : "${GOOGLE_CLOUD_LOCATION:?}"; : "${MODEL:?}"; : "${AGENT_ENGINE_ID:?run deploy_agent_engine.sh first}"

gcloud run deploy clearcut-web \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --source . \
  --region="$GOOGLE_CLOUD_LOCATION" \
  --allow-unauthenticated \
  --memory=1Gi --cpu=1 --timeout=900 --concurrency=20 --max-instances=3 \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=1,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=$GOOGLE_CLOUD_LOCATION,MODEL=$MODEL,MODEL_LOCATION=${MODEL_LOCATION:-global},AGENT_ENGINE_ID=$AGENT_ENGINE_ID"
