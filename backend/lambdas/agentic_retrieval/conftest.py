import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))


class FakeAgentEventMessage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeCamelModel:
    """Minimal stub that supports model_dump(by_alias=True)."""
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def model_dump(self, **kwargs):
        return self.__dict__.copy()


class FakeAnswerEventType(FakeCamelModel):
    def __init__(self, event="start", query_id="", **kw):
        super().__init__(response_type="answer-event", event=event, query_id=query_id, **kw)


class FakeFragmentContent(FakeCamelModel):
    def __init__(self, fragment="", **kw):
        super().__init__(fragment=fragment, **kw)


class FakeFragmentMessage(FakeCamelModel):
    def __init__(self, query_id="", content=None, **kw):
        super().__init__(response_type="fragment", query_id=query_id, content=content, **kw)

    def model_dump(self, **kwargs):
        d = self.__dict__.copy()
        if hasattr(d.get("content"), "model_dump"):
            d["content"] = d["content"].model_dump(**kwargs)
        return d


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

# Ensure real-ish stubs for models used in streaming callbacks.
sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage
sys.modules["websocket_utils.models"].AnswerEventType = FakeAnswerEventType
sys.modules["websocket_utils.models"].FragmentContent = FakeFragmentContent
sys.modules["websocket_utils.models"].FragmentMessage = FakeFragmentMessage
