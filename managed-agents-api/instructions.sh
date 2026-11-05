#!/usr/bin/env bash
set -euo pipefail

echo "==================================================================="
echo " Lab 5: Managed Agents API & Low-Infra Harness - Setup"
echo "==================================================================="

export PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
export USER_EMAIL=$(gcloud config get-value account 2>/dev/null)
export MCP_REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
export LOCATION="global"
export SERVICE_NAME="knowledge-catalog-mcp"

if [ -z "$PROJECT_ID" ]; then
    echo "ERROR: No active Google Cloud project set in gcloud."
    echo "Run 'gcloud config set project <your-project-id>' first."
    exit 1
fi

if [ -z "$USER_EMAIL" ]; then
    echo "ERROR: No active Google account found. Run 'gcloud auth login' first."
    exit 1
fi

export PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

RUN_SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${MCP_REGION}" \
    --format='value(status.url)' 2>/dev/null || true)

if [ -z "$RUN_SERVICE_URL" ]; then
    echo "WARNING: Cloud Run service '${SERVICE_NAME}' not found in '${MCP_REGION}'."
    echo "Deploy Lab 2 (create-knowledge-catalog-mcp-server) first, or export CATALOG_MCP_URL manually."
    export CATALOG_MCP_URL="${CATALOG_MCP_URL:-https://knowledge-catalog-mcp-placeholder.a.run.app/sse}"
else
    export CATALOG_MCP_URL="${RUN_SERVICE_URL}/sse"
fi

export AGENT_SKILL_GCS_BUCKET="${AGENT_SKILL_GCS_BUCKET:-${PROJECT_ID}-managed-agent-skills}"

echo "Active Project:   ${PROJECT_ID} (Number: ${PROJECT_NUM})"
echo "Active Account:   ${USER_EMAIL}"
echo "Agent Location:   ${LOCATION}"
echo "Catalog MCP URL:  ${CATALOG_MCP_URL}"
echo "Skill GCS Bucket: gs://${AGENT_SKILL_GCS_BUCKET}"

echo -e "\n=== Step 1: Enabling Required APIs ==="
gcloud services enable \
    aiplatform.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    --project="${PROJECT_ID}"

echo "[OK] APIs enabled."

echo -e "\n=== Step 2: Configuring User & Platform Service Agent IAM ==="
for role in "roles/aiplatform.user" "roles/bigquery.jobUser" "roles/bigquery.dataViewer"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="user:${USER_EMAIL}" \
        --role="${role}" \
        --condition=None >/dev/null
done

gcloud beta services identity create --service=aiplatform.googleapis.com --project="${PROJECT_ID}" 2>/dev/null || true
SA_EMAIL="service-${PROJECT_NUM}@gcp-sa-aiplatform.iam.gserviceaccount.com"

for role in "roles/aiplatform.serviceAgent" "roles/storage.objectViewer" "roles/bigquery.jobUser" "roles/bigquery.dataViewer"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${role}" \
        --condition=None >/dev/null 2>&1 || true
done

echo "[OK] IAM roles verified for ${USER_EMAIL} and ${SA_EMAIL}."

echo -e "\n=== Step 3: Staging Portable SKILL.md Package to Cloud Storage ==="
if ! gcloud storage buckets describe "gs://${AGENT_SKILL_GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${AGENT_SKILL_GCS_BUCKET}" \
        --project="${PROJECT_ID}" \
        --location="${MCP_REGION}" \
        --uniform-bucket-level-access
fi

gcloud storage cp -r skills/tokenomics-analytics "gs://${AGENT_SKILL_GCS_BUCKET}/" >/dev/null
echo "[OK] Staged skills/tokenomics-analytics/SKILL.md -> gs://${AGENT_SKILL_GCS_BUCKET}/tokenomics-analytics/SKILL.md"

echo -e "\n=== Step 4: Installing Minimal Python Dependencies ==="
pip install --upgrade -r requirements.txt

echo -e "\n==================================================================="
echo " Setup Complete - Export These Variables:"
echo "==================================================================="
echo "  export GOOGLE_CLOUD_PROJECT=\"${PROJECT_ID}\""
echo "  export GOOGLE_CLOUD_LOCATION=\"global\""
echo "  export CATALOG_MCP_URL=\"${CATALOG_MCP_URL}\""
echo "  export AGENT_SKILL_GCS_BUCKET=\"${AGENT_SKILL_GCS_BUCKET}\""
echo ""
echo "Next steps:"
echo "  1. Provision the Managed Agent:  python create_managed_agent.py"
echo "  2. Run governance & state tests: python test_managed_agent.py"
echo "==================================================================="
