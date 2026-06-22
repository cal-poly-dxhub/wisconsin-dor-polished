"""Structured logging and WebSocket trace emission for the agentic loop."""

import asyncio
import json
import logging
import time
from typing import Any

from websocket_utils.models import AgentEventMessage

logger = logging.getLogger(__name__)

ALLOWED_METADATA_KEYS = frozenset({
    "chunkCount",
    "docCount",
    "docId",
    "neighborCount",
    "topScore",
    "faqCount",
    "documentCount",
    "chainLength",
    "opinionChars",
    "refined",
    "citedDocCount",
    "latencyMs",
})


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"...[truncated {len(value) - max_chars} chars]"


def compact_log_value(value: Any, max_chars: int) -> Any:
    """Bound nested log fields so CloudWatch events stay queryable."""
    if isinstance(value, str):
        return truncate_text(value, max_chars)
    if isinstance(value, dict):
        return {str(k): compact_log_value(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        compact = [compact_log_value(v, max_chars) for v in value[:10]]
        if len(value) > 10:
            compact.append(f"...[{len(value) - 10} more]")
        return compact
    return value


def log_agent_event(
    event: str,
    level: int = logging.INFO,
    *,
    log_enabled: bool,
    max_chars: int,
    **fields: Any,
) -> None:
    if not log_enabled and level < logging.WARNING:
        return
    payload = {
        "component": "graphrag.agentic_retrieval",
        "event": event,
        **fields,
    }
    logger.log(
        level,
        json.dumps(compact_log_value(payload, max_chars), default=str, separators=(",", ":")),
    )


def query_log_fields(query: str, *, log_query_text: bool, max_chars: int) -> dict[str, Any]:
    import hashlib

    fields: dict[str, Any] = {
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest()[:16],
        "query_chars": len(query),
    }
    if log_query_text:
        fields["query_preview"] = truncate_text(query, max_chars)
    return fields


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
