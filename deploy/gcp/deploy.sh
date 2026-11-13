#!/usr/bin/env bash
# Deploy the whole app (FastAPI API + built Svelte frontend) to Google Cloud Run.
#
# Prerequisites (one time):
#   1. Install the gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login
#   3. A GCP project with billing enabled.
#
# Usage:
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
  artifactregistry.googleapis.com secretmanager.googleapis.com

# Ollama key from ../../.env -> Secret Manager (created once, new version on reruns).
KEY="$(grep -E '^OLLAMA_API_KEY=' "$ROOT/.env" | cut -d= -f2- || true)"
if [ -n "$KEY" ]; then
  if ! gcloud secrets describe ollama-api-key >/dev/null 2>&1; then
    gcloud secrets create ollama-api-key --replication-policy=automatic
  fi
  printf '%s' "$KEY" | gcloud secrets versions add ollama-api-key --data-file=-
  PNUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  gcloud secrets add-iam-policy-binding ollama-api-key \
    --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  SECRET_FLAG=(--set-secrets "OLLAMA_API_KEY=ollama-api-key:latest")
else
  echo "No OLLAMA_API_KEY in .env: chat will use the rule-based fallback."
  SECRET_FLAG=()
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
  --set-env-vars "DATA_DIR=/tmp/data,MAX_WORKERS=3" \
  "${SECRET_FLAG[@]}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
curl -fsS "$URL/api/health" && echo
echo "App: $URL"
