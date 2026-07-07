"""Convenience wrappers that bind Lambda config to the tracing primitives.

These read config at call time so tests can patch `config.LOG_AGENT_TRACE`
etc. without re-importing this module.
"""

import logging
from typing import Any

import config

from .emitter import emit_trace
from .logger import log_agent_event, query_log_fields
from .summaries import (
    build_tool_call_summary,
    build_tool_result_summary,
    summarize_assistant_message,
)


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    log_agent_event(
        event,
        level,
        log_enabled=config.LOG_AGENT_TRACE,
        max_chars=config.LOG_MAX_TEXT_CHARS,
        **fields,
    )


def query_fields(query: str) -> dict[str, Any]:
    return query_log_fields(
        query, log_query_text=config.LOG_QUERY_TEXT, max_chars=config.LOG_MAX_TEXT_CHARS
    )


def emit(ws_server, trace_seq, **kwargs) -> None:
    emit_trace(
        ws_server,
        trace_seq,
        emit_enabled=config.EMIT_AGENT_TRACE,
        max_chars=config.LOG_MAX_TEXT_CHARS,
        **kwargs,
    )


def tool_call_summary(tool_name: str, tool_input: dict) -> str:
    return build_tool_call_summary(tool_name, tool_input, config.neptune)


def tool_result_summary(tool_name: str, result: dict) -> dict:
    return build_tool_result_summary(tool_name, result, config.neptune)


def assistant_summary(message: dict) -> dict[str, Any]:
    return summarize_assistant_message(message, config.LOG_MAX_TEXT_CHARS)
