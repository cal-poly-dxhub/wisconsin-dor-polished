#!/bin/bash
set -euo pipefail

PHASE="${PHASE:-}"
CONFIG="/app/tools/ingestion/config/ingest_config.yaml"

# Optional env → flag mappings (all set by scripts/run_fargate.sh):
#   CACHE_PREFIX   → --cache-prefix <p>   (staging runs: "staging/")
#   GRAPH_ID       → --graph-id           (task-def default; container override wins)
#   ALIASES        → --aliases            (extract: generate chunk aliases)
#   ALIASES_ONLY   → --aliases-only       (extract: backfill aliases, no re-extraction)
#   EMBED_INPUT    → --embed-input plain|enriched
CACHE_PREFIX="${CACHE_PREFIX:-}"

run_extract() {
  python -m tools.ingestion.extract \
    --raw-bucket "${RAW_BUCKET}" \
    --work-bucket "${WORK_BUCKET}" \
    --config "$CONFIG" \
    --max-workers "${MAX_WORKERS:-3}" \
    ${CACHE_PREFIX:+--cache-prefix "$CACHE_PREFIX"} \
    ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
    ${FORCE:+--force} \
    ${SMART:+--smart} \
    ${RECLASSIFY:+--reclassify} \
    ${ALIASES:+--aliases} \
    ${ALIASES_ONLY:+--aliases-only}
}

run_embed() {
  python -m tools.ingestion.embed \
    --work-bucket "${WORK_BUCKET}" \
    --config "$CONFIG" \
    --max-workers "${MAX_WORKERS:-5}" \
    ${CACHE_PREFIX:+--cache-prefix "$CACHE_PREFIX"} \
    ${EMBED_INPUT:+--embed-input "$EMBED_INPUT"} \
    ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
    ${FORCE:+--force} \
    ${SMART:+--smart}
}

run_load() {
  python -m tools.ingestion.load \
    --work-bucket "${WORK_BUCKET}" \
    --graph-id "${GRAPH_ID}" \
    --config "$CONFIG" \
    ${CACHE_PREFIX:+--cache-prefix "$CACHE_PREFIX"} \
    ${SOURCE_FILTER:+--source-filter "$SOURCE_FILTER"} \
    "$@"
}

case "$PHASE" in
  extract)
    run_extract
    ;;
  embed)
    run_embed
    ;;
  load)
    run_load \
      ${START_PHASE:+--start-phase "$START_PHASE"} \
      ${STOP_AFTER_PHASE:+--stop-after-phase "$STOP_AFTER_PHASE"}
    ;;
  full)
    echo "=== Running full pipeline: extract → embed → load ==="
    [[ -n "$CACHE_PREFIX" ]] && echo "    cache prefix: $CACHE_PREFIX  graph: ${GRAPH_ID}"
    echo "--- Phase: extract ---"
    run_extract
    echo "--- Phase: embed ---"
    run_embed
    echo "--- Phase: load ---"
    run_load
    echo "=== Full pipeline complete ==="
    ;;
  *)
    echo "ERROR: PHASE must be one of: extract, embed, load, full"
    echo "Got: '$PHASE'"
    exit 1
    ;;
esac
