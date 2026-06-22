#!/bin/bash
set -euo pipefail

PHASE="${PHASE:-}"
CONFIG="/app/tools/graphrag/ingest_config.yaml"

case "$PHASE" in
  extract)
    exec python -m tools.graphrag.extract \
      --raw-bucket "${RAW_BUCKET}" \
      --work-bucket "${WORK_BUCKET}" \
      --config "$CONFIG" \
      --max-workers "${MAX_WORKERS:-3}" \
      ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
      ${FORCE:+--force}
    ;;
  embed)
    exec python -m tools.graphrag.embed \
      --work-bucket "${WORK_BUCKET}" \
      --config "$CONFIG" \
      --max-workers "${MAX_WORKERS:-5}" \
      ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
      ${FORCE:+--force}
    ;;
  load)
    exec python -m tools.graphrag.load \
      --work-bucket "${WORK_BUCKET}" \
      --graph-id "${GRAPH_ID}" \
      --config "$CONFIG" \
      ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
      ${START_PHASE:+--start-phase "$START_PHASE"} \
      ${STOP_AFTER_PHASE:+--stop-after-phase "$STOP_AFTER_PHASE"}
    ;;
  *)
    echo "ERROR: PHASE must be one of: extract, embed, load"
    echo "Got: '$PHASE'"
    exit 1
    ;;
esac
