"""Test setup for the agentic_retrieval Lambda package.

Puts the Lambda root (parent of this tests/ dir) and backend/layers on
sys.path so the package's absolute imports (`config`, `loop.phase_a`, ...)
and the real layer packages (`step_function_types`, `websocket_utils`)
resolve exactly as they do inside the deployed Lambda.

Tests that need module-level AWS clients re-imported fresh (e.g. handler
tests) should use the ``fresh_modules`` helper below instead of mutating
``sys.modules`` entries shared with other test files.
"""

import importlib
import os
import sys
from unittest.mock import patch

import pytest

_LAMBDA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LAYERS_ROOT = os.path.abspath(os.path.join(_LAMBDA_ROOT, "..", "..", "layers"))

for _p in (_LAMBDA_ROOT, _LAYERS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Every module belonging to this Lambda package. fresh_modules() purges these
# so a re-import re-executes module-level client init under the test's patches.
PACKAGE_MODULES = [
    "handler",
    "config",
    "loop",
    "loop.heartbeat",
    "loop.phase_a",
    "loop.phase_b",
    "agent_tools",
    "agent_tools.definitions",
    "agent_tools.executor",
    "graph",
    "graph.neptune_client",
    "streaming",
    "streaming.bedrock",
    "streaming.delivery",
    "tracing",
    "tracing.logger",
    "tracing.emitter",
    "tracing.runtime",
    "tracing.summaries",
    "case_law",
    "chat_history",
    "disambiguation",
    "faq",
    "prompt",
    "_prompt_fallback",
    "rag_documents",
    "wpam_dedup",
]


def _fresh_modules(*names: str, env: dict[str, str] | None = None):
    """Purge and re-import package modules with AWS deps mocked.

    Returns the imported modules in the order requested. All module-level
    boto3 clients become MagicMocks, so no test ever touches the network.
    """
    for mod in PACKAGE_MODULES:
        sys.modules.pop(mod, None)

    env_vars = {
        "AWS_REGION": "us-east-1",
        "RAW_BUCKET": "test-bucket",
        "CHAT_HISTORY_TABLE_NAME": "",
        "EMIT_AGENT_TRACE": "true",
        **(env or {}),
    }
    with (
        patch.dict(os.environ, env_vars),
        patch("boto3.client"),
        patch("boto3.resource"),
    ):
        # Patch the NeptuneClient class before config.py instantiates it so
        # config.neptune becomes a plain MagicMock (mirrors production wiring
        # without any real query machinery).
        graph_mod = importlib.import_module("graph.neptune_client")
        with patch.object(graph_mod, "NeptuneClient"):
            return tuple(importlib.import_module(name) for name in names)


@pytest.fixture
def fresh_modules():
    """Fixture handing tests the fresh-import helper (see _fresh_modules)."""
    return _fresh_modules
