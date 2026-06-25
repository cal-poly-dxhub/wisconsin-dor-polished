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
#
# Check whether the real websocket_utils package is importable (on sys.path
# via backend/conftest.py) — if so, import it rather than stubbing.
import importlib.util as _ilu

_ws_real = _ilu.find_spec("websocket_utils") is not None
_sft_real = _ilu.find_spec("step_function_types") is not None

if _ws_real:
    import websocket_utils
    import websocket_utils.models
    import websocket_utils.utils
else:
    for mod_name in [
        "websocket_utils", "websocket_utils.batching",
        "websocket_utils.models", "websocket_utils.utils",
    ]:
        sys.modules.setdefault(mod_name, MagicMock())
    sys.modules["websocket_utils.models"].AgentEventMessage = FakeAgentEventMessage
    sys.modules["websocket_utils.models"].AnswerEventType = FakeAnswerEventType
    sys.modules["websocket_utils.models"].FragmentContent = FakeFragmentContent
    sys.modules["websocket_utils.models"].FragmentMessage = FakeFragmentMessage

if _sft_real:
    import step_function_types
    import step_function_types.models
    import step_function_types.errors
else:
    for mod_name in [
        "step_function_types", "step_function_types.errors",
        "step_function_types.models",
    ]:
        sys.modules.setdefault(mod_name, MagicMock())
