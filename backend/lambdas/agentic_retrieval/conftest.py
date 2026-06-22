import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))


class FakeAgentEventMessage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


# Lambda layer mocks — only install if not already a real package.
# This prevents pollution when pytest collects across directories
# (e.g., running all of backend/ where layers/ has the real modules on path).
_LAYER_STUBS = {
    "websocket_utils": MagicMock(),
    "websocket_utils.batching": MagicMock(),
    "websocket_utils.models": MagicMock(),
    "websocket_utils.utils": MagicMock(),
    "step_function_types": MagicMock(),
    "step_function_types.errors": MagicMock(),
    "step_function_types.models": MagicMock(),
}

for mod_name, stub in _LAYER_STUBS.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = stub

# Ensure AgentEventMessage is always the fake class, not a MagicMock.
sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage
