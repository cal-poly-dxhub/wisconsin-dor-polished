#!/bin/bash
# Run the full ingestion pipeline (extract → embed → load) on Fargate.
# Each phase waits for the previous to complete before starting the next.
#
# Usage:
#   ./run_full_ingest.sh [options]
#
# Options:
#   --source-filter <prefix>  Only process docs matching this prefix
#   --force                   Re-process all (ignore cache)
#   --max-workers <N>         Override default worker count
#
# Examples:
#   ./run_full_ingest.sh
#   ./run_full_ingest.sh --source-filter wpam- --force
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# AWS profile: honors AWS_PROFILE, falls back to the CLI's "default" profile.
PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-WisconsinBotGraphRAG}"

# Pass all args through to each phase
ARGS=("$@")

PHASES=("extract" "embed" "load")

for phase in "${PHASES[@]}"; do
  echo "============================================"
  echo "  Starting phase: $phase"
  echo "============================================"
  echo ""

  # Run the phase and capture the task ARN from stdout
  TASK_OUTPUT=$("$SCRIPT_DIR/run_fargate.sh" "$phase" "${ARGS[@]}" 2>&1)
  echo "$TASK_OUTPUT"

  TASK_ARN=$(echo "$TASK_OUTPUT" | grep "Task started:" | awk '{print $3}')
  CLUSTER_ARN=$(echo "$TASK_OUTPUT" | grep "Cluster:" | awk '{print $2}')

  if [[ -z "$TASK_ARN" ]]; then
    echo "ERROR: Failed to start $phase task"
    exit 1
  fi

  # Poll until task completes
  echo ""
  echo "Waiting for $phase to complete..."
  while true; do
    STATUS=$(aws ecs describe-tasks \
      --cluster "$CLUSTER_ARN" \
      --tasks "$TASK_ARN" \
      --profile "$PROFILE" \
      --region "$REGION" \
      --query "tasks[0].lastStatus" \
      --output text 2>/dev/null || echo "UNKNOWN")

    if [[ "$STATUS" == "STOPPED" ]]; then
      # Check exit code
      EXIT_CODE=$(aws ecs describe-tasks \
        --cluster "$CLUSTER_ARN" \
        --tasks "$TASK_ARN" \
        --profile "$PROFILE" \
        --region "$REGION" \
        --query "tasks[0].containers[0].exitCode" \
        --output text 2>/dev/null || echo "1")

      if [[ "$EXIT_CODE" != "0" ]]; then
        echo ""
        echo "ERROR: Phase '$phase' failed with exit code $EXIT_CODE"
        echo "Check logs: aws logs tail /ecs/wis-dor-ingestion --follow --profile $PROFILE --region $REGION"
        exit 1
      fi

      echo "Phase '$phase' completed successfully."
      break
    fi

    printf "\r  Status: %-12s" "$STATUS"
    sleep 15
  done
  echo ""
done

echo ""
echo "============================================"
echo "  Full ingestion pipeline complete!"
echo "============================================"
