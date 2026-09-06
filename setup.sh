#!/bin/bash
# One-time Google Cloud project setup for ClearCut.
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?}"; : "${GOOGLE_CLOUD_LOCATION:?}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud services enable aiplatform.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  storage.googleapis.com artifactregistry.googleapis.com cloudtrace.googleapis.com

# Cloud Run's default service account must be allowed to call Agent Engine / Vertex AI.
PROJECT_NUMBER="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user" --condition=None >/dev/null
echo "Setup complete for $GOOGLE_CLOUD_PROJECT."
