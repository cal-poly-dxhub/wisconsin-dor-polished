#!/bin/bash
# Run a single ingestion phase on Fargate.
#
# Usage:
#   ./run_fargate.sh <phase> [options]
#
# Phases: extract, embed, load
#
# Options:
#   --source-filter <prefix>  Only process docs matching this prefix
#   --force                   Re-process all (ignore cache)
#   --smart                   (extract only) Only re-extract docs with stale cache
#   --start-phase <N>         (load only) Resume from sub-phase N
#   --stop-after-phase <N>    (load only) Stop after sub-phase N
#   --max-workers <N>         Override default worker count
#
# Examples:
#   ./run_fargate.sh extract
#   ./run_fargate.sh extract --source-filter wpam- --force
#   ./run_fargate.sh load --start-phase 5 --stop-after-phase 8
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${AWS_PROFILE:-widor}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-WisconsinBotGraphRAG}"

PHASE="${1:-}"
if [[ -z "$PHASE" || ! "$PHASE" =~ ^(extract|embed|load)$ ]]; then
  echo "Usage: $0 <extract|embed|load> [options]"
  exit 1
fi
shift

# Parse optional arguments
SOURCE_FILTER=""
FORCE=""
SMART=""
RECLASSIFY=""
START_PHASE=""
STOP_AFTER_PHASE=""
MAX_WORKERS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-filter) SOURCE_FILTER="$2"; shift 2 ;;
    --force) FORCE="true"; shift ;;
    --smart) SMART="true"; shift ;;
    --reclassify) RECLASSIFY="true"; shift ;;
    --start-phase) START_PHASE="$2"; shift 2 ;;
    --stop-after-phase) STOP_AFTER_PHASE="$2"; shift 2 ;;
    --max-workers) MAX_WORKERS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Fetch stack outputs
echo "Fetching stack outputs from $STACK_NAME..."
OUTPUTS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --profile "$PROFILE" \
  --region "$REGION" \
  --query "Stacks[0].Outputs" \
  --output json)

get_output() {
  echo "$OUTPUTS" | python3 -c "
import sys, json
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == '$1':
        print(o['OutputValue'])
        break
"
}

CLUSTER_ARN=$(get_output "IngestionClusterArn")
TASK_DEF_ARN=$(get_output "IngestionTaskDefinitionArn")

# Get nested stack outputs for networking
NESTED_OUTPUTS=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" \
  --region "$REGION" \
  --query "Stacks[?contains(StackName, 'IngestionStack')].Outputs[]" \
  --output json)

SUBNET_IDS=$(echo "$NESTED_OUTPUTS" | python3 -c "
import sys, json
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'SubnetIds':
        print(o['OutputValue'])
        break
")

SECURITY_GROUP_ID=$(echo "$NESTED_OUTPUTS" | python3 -c "
import sys, json
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'SecurityGroupId':
        print(o['OutputValue'])
        break
")

# Build environment overrides
ENV_OVERRIDES='[{"name":"PHASE","value":"'"$PHASE"'"}'
[[ -n "$SOURCE_FILTER" ]] && ENV_OVERRIDES+=',{"name":"SOURCE_FILTER","value":"'"$SOURCE_FILTER"'"}'
[[ -n "$FORCE" ]] && ENV_OVERRIDES+=',{"name":"FORCE","value":"true"}'
[[ -n "$SMART" ]] && ENV_OVERRIDES+=',{"name":"SMART","value":"true"}'
[[ -n "$RECLASSIFY" ]] && ENV_OVERRIDES+=',{"name":"RECLASSIFY","value":"true"}'
[[ -n "$START_PHASE" ]] && ENV_OVERRIDES+=',{"name":"START_PHASE","value":"'"$START_PHASE"'"}'
[[ -n "$STOP_AFTER_PHASE" ]] && ENV_OVERRIDES+=',{"name":"STOP_AFTER_PHASE","value":"'"$STOP_AFTER_PHASE"'"}'
[[ -n "$MAX_WORKERS" ]] && ENV_OVERRIDES+=',{"name":"MAX_WORKERS","value":"'"$MAX_WORKERS"'"}'
ENV_OVERRIDES+=']'

# Convert subnet IDs to JSON array
IFS=',' read -ra SUBNETS <<< "$SUBNET_IDS"
SUBNET_JSON=$(printf '"%s",' "${SUBNETS[@]}")
SUBNET_JSON="[${SUBNET_JSON%,}]"

echo ""
echo "Running ingestion phase: $PHASE"
echo "  Cluster: $CLUSTER_ARN"
echo "  Task Definition: $TASK_DEF_ARN"
[[ -n "$SOURCE_FILTER" ]] && echo "  Source Filter: $SOURCE_FILTER"
[[ -n "$FORCE" ]] && echo "  Force: true"
[[ -n "$SMART" ]] && echo "  Smart: true"
[[ -n "$RECLASSIFY" ]] && echo "  Reclassify: true"
echo ""

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER_ARN" \
  --task-definition "$TASK_DEF_ARN" \
  --launch-type FARGATE \
  --network-configuration "{
    \"awsvpcConfiguration\": {
      \"subnets\": $SUBNET_JSON,
      \"securityGroups\": [\"$SECURITY_GROUP_ID\"],
      \"assignPublicIp\": \"ENABLED\"
    }
  }" \
  --overrides "{
    \"containerOverrides\": [{
      \"name\": \"ingestion\",
      \"environment\": $ENV_OVERRIDES
    }]
  }" \
  --profile "$PROFILE" \
  --region "$REGION" \
  --query "tasks[0].taskArn" \
  --output text)

echo "Task started: $TASK_ARN"
echo ""
echo "Monitor logs:"
echo "  aws logs tail /ecs/wis-dor-ingestion --follow --profile $PROFILE --region $REGION"
echo ""
echo "Check status:"
echo "  aws ecs describe-tasks --cluster $CLUSTER_ARN --tasks $TASK_ARN --profile $PROFILE --region $REGION --query 'tasks[0].lastStatus'"
