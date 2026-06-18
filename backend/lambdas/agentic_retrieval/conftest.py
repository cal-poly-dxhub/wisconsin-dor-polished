import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))

# test_agentic_retrieval.py mocks these at module import time.
# Clean them from sys.modules BEFORE other directories are collected.
# This conftest's pytest_configure runs early enough.

_MOCKED_MODULES = [
    "step_function_types", "step_function_types.errors", "step_function_types.models",
    "websocket_utils", "websocket_utils.models", "websocket_utils.utils",
]


def pytest_unconfigure(config):
    """Remove mock modules when pytest finishes with this conftest's scope."""
    for mod_name in _MOCKED_MODULES:
        mod = sys.modules.get(mod_name)
        if isinstance(mod, MagicMock):
            del sys.modules[mod_name]
