#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Cloud Run without a key file.
#
# Creates (idempotently):
#   * a Workload Identity Federation pool + OIDC provider that trusts GitHub's
#     tokens ONLY for this repository's main branch;
#   * a `github-deployer` service account with just the roles a source deploy
#     needs (Cloud Run, Cloud Build, the source bucket, Artifact Registry);
#   * the GitHub repository variables the workflow reads (needs `gh` logged in).
#
# Usage:
#   PROJECT_ID=my-project REPO=owner/name ./deploy/gcp/setup-cicd.sh
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}" "${REPO:?set REPO (owner/name)}"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-vfsim-repo}"
SA_NAME="${SA_NAME:-github-deployer}"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com
PNUM="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${PNUM}-compute@developer.gserviceaccount.com"

if ! gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location=global \
    --display-name="GitHub Actions"
fi
# Only tokens minted for pushes to main of $REPO are accepted.
CONDITION="assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --location=global --workload-identity-pool="$POOL" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="$CONDITION"
fi

if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="GitHub Actions deployer"
fi
for role in run.admin cloudbuild.builds.editor storage.admin artifactregistry.writer \
            serviceusage.serviceUsageConsumer logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" \
    --role="roles/$role" --condition=None >/dev/null
done
# Deploying a service that runs as (and builds with) the default compute account.
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$SA" --role=roles/iam.serviceAccountUser >/dev/null
# Let the repository's workflow impersonate the deployer.
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" >/dev/null

WIF="projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
gh variable set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT_ID"
gh variable set GCP_WIF_PROVIDER --repo "$REPO" --body "$WIF"
gh variable set GCP_DEPLOY_SA --repo "$REPO" --body "$SA"
echo "CI/CD ready: provider $WIF, service account $SA"
