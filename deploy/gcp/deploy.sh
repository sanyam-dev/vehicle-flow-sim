#!/usr/bin/env bash
# Deploy the whole app (FastAPI API + built Svelte frontend) to Google Cloud Run.
#
# Prerequisites (one time):
#   1. Install the gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login
#   3. A GCP project with billing enabled.
#
# Usage:
#   PROJECT_ID=my-project ./deploy/gcp/deploy-ollama.sh   # chat LLM, once
#   PROJECT_ID=my-project ./deploy/gcp/deploy.sh
#   (optional) REGION=asia-south1 SERVICE=vfsim
#
# Why these Cloud Run flags:
#   --no-cpu-throttling  simulations keep running in worker processes after
#                        POST /api/runs has returned; throttled CPU would stall them.
#   --min/max-instances 1  the run store lives on the instance's local disk, so
#                        every request for a run must reach the same instance.
#   --memory 4Gi --cpu 2  one OSM graph + a few concurrent per-session workers.
#   --timeout 3600       long polling-free requests are short, but allow area
#                        downloads (Overpass) on POST paths to finish.
# The local disk is ephemeral: the graph / geocode caches and run results are
# lost when the instance restarts (acceptable for v1).
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-vfsim}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# New projects no longer give the default compute service account (which Cloud
# Build uses for source deploys) the rights to read sources and push images.
PNUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None >/dev/null

# Chat LLM: the private Ollama GPU service from deploy-ollama.sh, called with
# the app's Google ID token. Without it the chat uses the keyword parser.
OLLAMA_REGION="${OLLAMA_REGION:-asia-southeast1}"
OLLAMA_URL="$(gcloud run services describe "${OLLAMA_SERVICE:-ollama}" \
  --region "$OLLAMA_REGION" --format='value(status.url)' 2>/dev/null || true)"
if [ -n "$OLLAMA_URL" ]; then
  LLM_ENV="LLM_PROVIDER=ollama,OLLAMA_HOST=${OLLAMA_URL},OLLAMA_AUTH=gcp,LLM_MODEL=${MODEL:-qwen3:8b},LLM_THINK=false"
else
  echo "No Ollama service found (run deploy-ollama.sh): chat will use the keyword parser."
  LLM_ENV="LLM_PROVIDER=rules"
fi

# Build the root Dockerfile (frontend + backend) with Cloud Build and deploy.
gcloud run deploy "$SERVICE" \
  --source "$ROOT" \
  --region "$REGION" \
  --port 8000 \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --min-instances 1 --max-instances 1 \
  --cpu 2 --memory 4Gi \
  --timeout 3600 \
  --clear-secrets \
  --set-env-vars "DATA_DIR=/tmp/data,MAX_WORKERS=3,${LLM_ENV}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
curl -fsS "$URL/api/health" && echo
echo "App: $URL"
