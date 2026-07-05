#!/usr/bin/env bash
set -euo pipefail

# Syncs FAQ files from the source bucket (us-west-2) to the GraphRAG FAQ bucket
# (us-east-1) and triggers a Bedrock KB ingestion job.
#
# Usage: ./tools/ingestion/scripts/sync_faq_bucket.sh [--profile PROFILE] [--stack-name STACK_NAME]

PROFILE="${AWS_PROFILE:-wisco}"
STACK_NAME="WisconsinBotGraphRAG"
SOURCE_BUCKET="wis-faq-bucket"
SOURCE_REGION="us-west-2"
TARGET_REGION="us-east-1"

while [[ $# -gt 0 ]]; do
  case $1 in
    --profile) PROFILE="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "==> Looking up GraphRAG FAQ bucket and KB IDs from CloudFormation stack outputs..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --profile "$PROFILE" \
    --region "$TARGET_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

TARGET_BUCKET=$(get_output "GraphRAGFaqBucketName")
FAQ_KB_ID=$(get_output "GraphRAGFaqKnowledgeBaseId")
FAQ_DS_ID=$(get_output "GraphRAGFaqDataSourceId")

if [[ -z "$TARGET_BUCKET" || -z "$FAQ_KB_ID" || -z "$FAQ_DS_ID" ]]; then
  echo "ERROR: Could not find required stack outputs. Ensure the stack is deployed with FAQ resources."
  echo "  TARGET_BUCKET=$TARGET_BUCKET"
  echo "  FAQ_KB_ID=$FAQ_KB_ID"
  echo "  FAQ_DS_ID=$FAQ_DS_ID"
  exit 1
fi

echo "  Source:      s3://$SOURCE_BUCKET (us-west-2)"
echo "  Target:      s3://$TARGET_BUCKET ($TARGET_REGION)"
echo "  KB ID:       $FAQ_KB_ID"
echo "  DataSource:  $FAQ_DS_ID"

echo ""
echo "==> Syncing FAQ files..."
aws s3 sync \
  "s3://$SOURCE_BUCKET" \
  "s3://$TARGET_BUCKET" \
  --source-region "$SOURCE_REGION" \
  --region "$TARGET_REGION" \
  --profile "$PROFILE"

COPIED_COUNT=$(aws s3 ls "s3://$TARGET_BUCKET/" --profile "$PROFILE" --region "$TARGET_REGION" | wc -l | tr -d ' ')
echo "  $COPIED_COUNT files in target bucket."

echo ""
echo "==> Starting Bedrock KB ingestion job..."
INGESTION_JOB=$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$FAQ_KB_ID" \
  --data-source-id "$FAQ_DS_ID" \
  --profile "$PROFILE" \
  --region "$TARGET_REGION" \
  --query "ingestionJob.ingestionJobId" \
  --output text)

echo "  Ingestion job started: $INGESTION_JOB"
echo ""
echo "==> Waiting for ingestion to complete..."

while true; do
  STATUS=$(aws bedrock-agent get-ingestion-job \
    --knowledge-base-id "$FAQ_KB_ID" \
    --data-source-id "$FAQ_DS_ID" \
    --ingestion-job-id "$INGESTION_JOB" \
    --profile "$PROFILE" \
    --region "$TARGET_REGION" \
    --query "ingestionJob.status" \
    --output text)

  echo "  Status: $STATUS"

  case "$STATUS" in
    COMPLETE) echo "==> Ingestion complete!"; break ;;
    FAILED)   echo "ERROR: Ingestion failed."; exit 1 ;;
    *)        sleep 10 ;;
  esac
done

echo ""
echo "==> Done. FAQ Knowledge Base is ready for queries."
