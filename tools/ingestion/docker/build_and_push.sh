#!/bin/bash
# Build the ingestion Docker image and push it to ECR.
#
# Usage:
#   ./build_and_push.sh
#
# Prerequisites:
#   - Docker running locally
#   - AWS credentials configured (profile: widor)
#   - CDK stack deployed (to create the ECR repository)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SCRIPT_DIR is tools/ingestion/docker → repo root is three levels up.
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PROFILE="${AWS_PROFILE:-widor}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-WisconsinBotGraphRAG}"

# Get ECR repository URI from nested stack outputs
echo "Fetching ECR repository URI..."
NESTED_OUTPUTS=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" \
  --region "$REGION" \
  --query "Stacks[?contains(StackName, 'IngestionStack')].Outputs[]" \
  --output json)

ECR_URI=$(echo "$NESTED_OUTPUTS" | python3 -c "
import sys, json
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'EcrRepositoryUri':
        print(o['OutputValue'])
        break
")

if [[ -z "$ECR_URI" ]]; then
  echo "ERROR: Could not find ECR repository URI. Is the stack deployed?"
  exit 1
fi

ACCOUNT_ID=$(echo "$ECR_URI" | cut -d. -f1)
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

echo "ECR URI: $ECR_URI"
echo ""

# Authenticate Docker with ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" --profile "$PROFILE" | \
  docker login --username AWS --password-stdin "$REGISTRY"

# Build the image from repo root (Dockerfile references tools/ paths)
echo ""
echo "Building Docker image..."
docker build --platform linux/amd64 -t wis-dor-ingestion:latest -f "$SCRIPT_DIR/Dockerfile" "$REPO_ROOT"

# Tag and push
echo ""
echo "Pushing to ECR..."
docker tag wis-dor-ingestion:latest "$ECR_URI:latest"
docker push "$ECR_URI:latest"

echo ""
echo "Done! Image pushed to: $ECR_URI:latest"
