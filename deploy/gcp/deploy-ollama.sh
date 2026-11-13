#!/usr/bin/env bash
# Deploy the chat agent's LLM: a private Ollama service on Cloud Run with one
# NVIDIA L4 GPU that scales to zero when idle. Run this before deploy.sh,
# which points the app at it.
#
# Usage:
#   PROJECT_ID=my-project ./deploy/gcp/deploy-ollama.sh
#   (optional) OLLAMA_REGION=asia-southeast1 MODEL=qwen3:8b
#
# Why these choices:
#   asia-southeast1      nearest region where Cloud Run L4 GPUs are generally
#                        available (asia-south1 GPUs are invitation-only).
#   --no-gpu-zonal-redundancy  cheaper and easier to get quota for; a zone
#                        outage only means the chat falls back to keywords.
#   --min-instances 0    no cost while nobody chats; the first chat after idle
#                        waits for a cold start (~30-60 s).
#   --no-cpu-throttling  required for GPU services.
#   --no-allow-unauthenticated  only the app's service account may call it.
#   Cloud Build e2-highcpu-8 + 1 h timeout: the image holds the ~5 GB model.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${OLLAMA_REGION:-asia-southeast1}"
SERVICE="${OLLAMA_SERVICE:-ollama}"
MODEL="${MODEL:-qwen3:8b}"
REPO=vfsim
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/ollama:${MODEL//:/-}"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
PNUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
APP_SA="${PNUM}-compute@developer.gserviceaccount.com"

if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --location "$REGION" \
    --repository-format docker --description "Vehicle Flow Simulator images"
fi

cat > "$HERE/ollama/cloudbuild.yaml" <<YAML
steps:
- name: gcr.io/cloud-builders/docker
  args: [build, --build-arg, MODEL=${MODEL}, -t, ${IMAGE}, .]
images: [${IMAGE}]
options: {machineType: E2_HIGHCPU_8, diskSizeGb: 100}
timeout: 3600s
YAML
gcloud builds submit "$HERE/ollama" --region "$REGION" \
  --config "$HERE/ollama/cloudbuild.yaml"

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --port 8080 \
  --no-allow-unauthenticated \
  --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu 4 --memory 16Gi \
  --no-cpu-throttling \
  --min-instances 0 --max-instances 1 \
  --concurrency 4 \
  --timeout 600

# The app (default compute service account) may call it.
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --member "serviceAccount:${APP_SA}" --role roles/run.invoker >/dev/null

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "Ollama: $URL (model $MODEL, private)"
