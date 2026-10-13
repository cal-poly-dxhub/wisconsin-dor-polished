"""PACKAGE_MODULES must name every module in the package, or fresh_modules()
leaves stale copies of the missing ones behind between tests."""

import os

from conftest import _LAMBDA_ROOT, PACKAGE_MODULES


def _package_modules() -> set[str]:
    found = set()
    for dirpath, dirnames, filenames in os.walk(_LAMBDA_ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "__pycache__")]
        rel = os.path.relpath(dirpath, _LAMBDA_ROOT)
        prefix = "" if rel == "." else rel.replace(os.sep, ".")
        for name in filenames:
            if not name.endswith(".py"):
                continue
            stem = name[:-3]
            if stem == "__init__":
                found.add(prefix)
            else:
                found.add(f"{prefix}.{stem}" if prefix else stem)
    return found


def test_package_modules_lists_every_module():
    assert set(PACKAGE_MODULES) == _package_modules()
