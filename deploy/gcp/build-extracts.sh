#!/usr/bin/env bash
# Build the offline road-network extracts (fallback when Overpass is down).
#
# Creates gs://${PROJECT_ID}-osm-extracts (if missing) and runs a Cloud Build
# job that downloads Geofabrik regions, keeps only roads, and uploads
# <id>.osm.pbf + <id>.poly + <id>.date per region. deploy.sh mounts the bucket
# read-only at /osm on Cloud Run (OSM_EXTRACTS_DIR=/osm).
#
# Usage:
#   PROJECT_ID=my-project ./deploy/gcp/build-extracts.sh
#   (optional) REGION=asia-south1  REGIONS="asia/india asia/india/southern-zone"
#
# Default coverage is all of India: the six zones (a query is cut from the
# smallest zone containing it, which keeps osmium fast) plus the whole-India
# file for areas straddling two zones. Rerun to refresh (monthly is plenty).
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
BUCKET="${BUCKET:-${PROJECT_ID}-osm-extracts}"
REGIONS="${REGIONS:-asia/india $(printf 'asia/india/%s-zone ' central eastern north-eastern northern southern western)}"
HERE="$(cd "$(dirname "$0")" && pwd)"

gcloud config set project "$PROJECT_ID"
gcloud services enable cloudbuild.googleapis.com storage.googleapis.com
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
    --uniform-bucket-level-access
fi

# The default compute service account runs both Cloud Build (writes) and the
# Cloud Run app (reads through the volume mount).
PNUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" \
  --role=roles/storage.objectAdmin >/dev/null

gcloud builds submit "$HERE/extracts" --region "$REGION" \
  --config "$HERE/extracts/cloudbuild.yaml" \
  --substitutions="_BUCKET=${BUCKET},_REGIONS=${REGIONS% }"
