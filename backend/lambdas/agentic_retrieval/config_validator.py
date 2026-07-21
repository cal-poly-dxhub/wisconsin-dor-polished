"""Cold-start env var validation against config/retrieval.toml.

Validates that any environment variable actually present in the Lambda's
environment matches the type/range documented in retrieval.toml. Vars that
are simply absent are left alone — each module's own hardcoded default
applies exactly as it did before this file existed (see
docs/spec-retrieval-pipeline-refactor.md, "Backward compatible").

This module is intentionally forgiving:
  - If retrieval.toml is missing (e.g. a partial deploy that hasn't picked
    up the new bundle source yet), ``validate_env()`` returns an empty
    report instead of raising, so cold start never fails because of this
    module.
  - If a value fails type/range validation, it is reported but NOT
    corrected or raised — the offending env var still wins at runtime
    (env vars remain the deploy-time override; this module only adds
    visibility via a CloudWatch log line at cold start).
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# retrieval.toml is bundled alongside this file at the Lambda root (see
# bundles.toml -> [[bundles]] dest = "agentic_retrieval"). Tests / local runs
# can override the search path via RETRIEVAL_TOML_PATH.
_DEFAULT_TOML_PATH = Path(__file__).resolve().parent / "retrieval.toml"


@dataclass
class ValidationIssue:
    env_var: str
    problem: str
    value: str


@dataclass
class ValidationReport:
    """Result of validate_env(): what was checked, and what was wrong."""

    toml_found: bool
    checked: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _load_toml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return tomllib.loads(path.read_text())
    except Exception:  # noqa: BLE001 — never let a malformed TOML break cold start
        logger.warning(
            "failed to parse retrieval.toml at %s; skipping validation", path, exc_info=True
        )
        return None


def _check_type(value: str, expected_type: str) -> str | None:
    """Return an error string if ``value`` doesn't parse as ``expected_type``."""
    if expected_type == "int":
        try:
            int(value)
        except ValueError:
            return f"expected int, got {value!r}"
    elif expected_type == "float":
        try:
            float(value)
        except ValueError:
            return f"expected float, got {value!r}"
    elif expected_type == "bool":
        if value.lower() not in ("true", "false"):
            return f"expected bool ('true'/'false'), got {value!r}"
    # "string" (or any other declared type) accepts anything.
    return None


def _check_range(value: str, expected_type: str, range_: list) -> str | None:
    if expected_type not in ("int", "float") or not range_ or len(range_) != 2:
        return None
    try:
        numeric = float(value) if expected_type == "float" else int(value)
    except ValueError:
        return None  # already reported by _check_type
    low, high = range_
    if numeric < low or numeric > high:
        return f"value {numeric} outside documented range [{low}, {high}]"
    return None


def validate_env(
    toml_path: Path | str | None = None,
    environ: dict | None = None,
) -> ValidationReport:
    """Validate env vars actually present in the environment against retrieval.toml.

    Args:
        toml_path: Override path to retrieval.toml (defaults to the copy
            bundled next to this file). Primarily for tests.
        environ: Override environment mapping (defaults to os.environ).

    Returns:
        A ValidationReport. Never raises — malformed config or a missing
        TOML file both result in a report describing what happened, not an
        exception, so a bad config file cannot fail Lambda cold start.
    """
    path = Path(toml_path) if toml_path is not None else _DEFAULT_TOML_PATH
    env = environ if environ is not None else os.environ

    config = _load_toml(path)
    if config is None:
        return ValidationReport(toml_found=False)

    env_schema = config.get("env", {})
    report = ValidationReport(toml_found=True)

    for key, spec in env_schema.items():
        value = env.get(key)
        if value is None:
            continue  # absent -> falls back to each module's own default
        report.checked += 1
        expected_type = spec.get("type", "string")

        type_error = _check_type(value, expected_type)
        if type_error:
            report.issues.append(ValidationIssue(env_var=key, problem=type_error, value=value))
            continue

        range_error = _check_range(value, expected_type, spec.get("range"))
        if range_error:
            report.issues.append(ValidationIssue(env_var=key, problem=range_error, value=value))

    return report


def validate_env_and_log(
    toml_path: Path | str | None = None,
    environ: dict | None = None,
) -> ValidationReport:
    """validate_env() + emit a single CloudWatch log line summarizing the result.

    Intended to be called once at cold start (see handler.py). Swallows all
    exceptions internally via validate_env()'s own error handling — this
    wrapper only adds logging, never raises.
    """
    report = validate_env(toml_path=toml_path, environ=environ)
    if not report.toml_found:
        logger.info(
            "retrieval.toml not found; skipping env var validation "
            "(falling back to each module's hardcoded defaults)"
        )
        return report

    if report.ok:
        logger.info(
            "retrieval.toml env var validation passed (%d var(s) checked)",
            report.checked,
        )
    else:
        for issue in report.issues:
            logger.warning(
                "retrieval.toml validation: %s=%r invalid — %s",
                issue.env_var,
                issue.value,
                issue.problem,
            )
    return report
