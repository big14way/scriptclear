#!/bin/bash
# Verifies that $MODEL exists in Vertex AI Model Garden for this project/region. Never hard-code the model.
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
: "${MODEL:?}"; : "${GOOGLE_CLOUD_LOCATION:?}"
TOKEN="$(gcloud auth print-access-token)"
URL="https://${GOOGLE_CLOUD_LOCATION}-aiplatform.googleapis.com/v1beta1/publishers/google/models/${MODEL}"
curl -sf -H "Authorization: Bearer $TOKEN" "$URL" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("OK:", d.get("name"), "| versionId:", d.get("versionId"), "| launchStage:", d.get("launchStage"))'
