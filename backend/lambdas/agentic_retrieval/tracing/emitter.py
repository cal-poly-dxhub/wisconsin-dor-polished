"""WebSocket agent-trace emission for the visualizer/trace UI."""

import asyncio
import logging
import time
from typing import Any

from websocket_utils.models import AgentEventMessage

from .logger import compact_log_value

logger = logging.getLogger(__name__)

ALLOWED_METADATA_KEYS = frozenset(
    {
        "chunkCount",
        "docCount",
        "docId",
        "docTitle",
        "heading",
        "neighborCount",
        "topScore",
        "faqCount",
        "documentCount",
        "chainLength",
        "opinionChars",
        "refined",
        "refinedQuery",
        "citedDocCount",
        "hasPlan",
        "latencyMs",
        "keywordFallback",
        "preDedupCount",
        "authorityBreakdown",
        "relationshipCounts",
        "discoveryCounts",
        "caseLawCount",
        "autoEnrichedCount",
        "scoreBuckets",
        "targetWpamYear",
        "discoveryTitles",
        "faqScoreThreshold",
        "faqScores",
        "sectionCount",
        "sectionHeadings",
        "docChunks",
        "topFaqSnippet",
        "neighborTitles",
        "neighborEdges",
        "ranked",
        "topK",
        "totalCandidates",
        "query",
        "sectionChunkCount",
        "returnedChunkCount",
        "mean",
        "std",
        "zThreshold",
        "flatDistribution",
        "chunkScores",
        "chunkIds",
        "statuteBackfill",
        "caselawBackfill",
        "caselawBackfillMeta",
        "broadDiscovery",
        "broadDocChunks",
        "broadFullDocChunks",
        "broadPreDedupCount",
        "broadKeptCount",
        "broadAuthorityBreakdown",
        "broadScoreBuckets",
        "broadTopScore",
        "broadQuery",
        "broadSkipped",
        "broadChunkCount",
        "totalChunkCount",
        "diversityCapPerDoc",
        "seeded",
        "filtered",
    }
)


def filter_metadata(metadata: Any) -> dict[str, Any]:
    """Drop any keys not in ALLOWED_METADATA_KEYS, log on drops."""
    if not isinstance(metadata, dict):
        return {}
    dropped = [k for k in metadata if k not in ALLOWED_METADATA_KEYS]
    if dropped:
        logger.warning(
            "trace metadata dropped disallowed key(s): %s",
            ", ".join(sorted(dropped)),
        )
    return {k: v for k, v in metadata.items() if k in ALLOWED_METADATA_KEYS}


def emit_trace(
    ws_server,
    trace_seq,
    *,
    emit_enabled: bool,
    query_id: str,
    kind: str,
    turn: int | None = None,
    payload: dict | None = None,
    dev_payload: dict | None = None,
    max_chars: int = 500,
) -> None:
    """Push an AgentEventMessage to the client. Best-effort — never raises."""
    if not emit_enabled or ws_server is None:
        return
    try:
        message = AgentEventMessage(
            query_id=query_id,
            kind=kind,
            turn=turn,
            seq=trace_seq(),
            timestamp=int(time.time() * 1000),
            payload=payload or {},
            dev_payload=compact_log_value(dev_payload or {}, max_chars),
        )
        asyncio.run(ws_server.send_json(message))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit agent-trace event", exc_info=True)
