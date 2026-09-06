#!/bin/bash
# Verifies that $MODEL exists in Vertex AI Model Garden for this project/region. Never hard-code the model.
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
: "${MODEL:?}"
LOC="${MODEL_LOCATION:-global}"
TOKEN="$(gcloud auth print-access-token)"
if [[ "$LOC" == "global" ]]; then HOST="aiplatform.googleapis.com"; else HOST="${LOC}-aiplatform.googleapis.com"; fi
URL="https://${HOST}/v1beta1/publishers/google/models/${MODEL}"
echo "Checking Model Garden: $URL"
curl -sf -H "Authorization: Bearer $TOKEN" "$URL" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("OK:", d.get("name"), "| versionId:", d.get("versionId"), "| launchStage:", d.get("launchStage"))'
