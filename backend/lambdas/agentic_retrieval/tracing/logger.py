"""Structured CloudWatch logging helpers for the agentic loop."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
