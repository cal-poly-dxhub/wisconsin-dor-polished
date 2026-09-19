"""Shared configuration for the agentic retrieval Lambda.

Centralizes env-var flags, tunables, and AWS client singletons so every
module reads the same values and tests can patch them in one place.
"""

import logging
import os

import boto3
from graph.neptune_client import NeptuneClient

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

REGION = os.environ.get("AWS_REGION", "us-east-1")
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")

AGENTIC_MODEL_ID = os.environ.get("AGENTIC_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
LOG_AGENT_TRACE = os.environ.get("LOG_AGENT_TRACE", "true").lower() == "true"
LOG_QUERY_TEXT = os.environ.get("LOG_QUERY_TEXT", "true").lower() == "true"
LOG_MAX_TEXT_CHARS = int(os.environ.get("LOG_MAX_TEXT_CHARS", "500"))
EMIT_AGENT_TRACE = os.environ.get("EMIT_AGENT_TRACE", "true").lower() == "true"
ENABLE_DISAMBIGUATION = os.environ.get("ENABLE_DISAMBIGUATION", "false").lower() == "true"
# Soft "start a new chat?" suggestion when a follow-up opens an unrelated topic.
# Gated separately from disambiguation so it can be rolled out independently.
ENABLE_TOPIC_SHIFT = os.environ.get("ENABLE_TOPIC_SHIFT", "false").lower() == "true"

# Post-retrieval adequacy judge (see adequacy_judge.py). When enabled, every
# query runs the Phase A research loop and the judge checks the answer plan
# against the cited chunks before Phase B streams.
ADEQUACY_JUDGE_ENABLED = os.environ.get("ADEQUACY_JUDGE_ENABLED", "false").lower() == "true"
# The pre-loop scope gate (the OUT_OF_SCOPE / DISAMBIGUATE short-circuits in
# disambiguation.classify_query). Turning it off is only meaningful with the
# judge on: retrieval then always runs and the judge decides after the fact.
# TOPIC_SHIFT is NOT part of this gate — it is about conversation continuity
# and stays under ENABLE_TOPIC_SHIFT.
SCOPE_GATE_ENABLED = os.environ.get("SCOPE_GATE_ENABLED", "true").lower() == "true"

MAX_TURNS = 10
WS_HEARTBEAT_INTERVAL = 15  # seconds between keepalive pings

# Bedrock KB relevance scores range 0-1. A well-matched FAQ typically scores
# 0.75+; loosely related hits land around 0.6-0.7. At/above this threshold the
# FAQ is treated as the primary source of truth for the answer, while the
# agentic loop still runs to supplement it with citable graph evidence.
FAQ_SCORE_THRESHOLD = 0.70

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
neptune = NeptuneClient()
