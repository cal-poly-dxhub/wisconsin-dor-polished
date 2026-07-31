"""Sanity checks for config/retrieval.toml — the source of truth for
agentic_retrieval env vars.

These tests parse the real repo-root config/retrieval.toml (not a fixture)
so a malformed edit to the file fails CI immediately.
"""

import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TOML_PATH = _REPO_ROOT / "config" / "retrieval.toml"

_VALID_TYPES = {"int", "float", "bool", "string"}


@pytest.fixture(scope="module")
def config():
    assert _TOML_PATH.exists(), f"config/retrieval.toml not found at {_TOML_PATH}"
    return tomllib.loads(_TOML_PATH.read_text())


def test_toml_parses(config):
    assert isinstance(config, dict)


def test_env_section_present_and_nonempty(config):
    assert "env" in config
    assert len(config["env"]) > 0


def test_every_env_entry_has_required_fields(config):
    for key, entry in config["env"].items():
        assert "default" in entry, f"{key} missing 'default'"
        assert "description" in entry, f"{key} missing 'description'"
        assert "stage" in entry, f"{key} missing 'stage'"
        assert "model_override" in entry, f"{key} missing 'model_override'"


def test_every_env_entry_type_is_valid(config):
    for key, entry in config["env"].items():
        entry_type = entry.get("type", "string")
        assert entry_type in _VALID_TYPES, f"{key} has invalid type {entry_type!r}"


def test_every_env_entry_default_matches_declared_type(config):
    for key, entry in config["env"].items():
        entry_type = entry.get("type", "string")
        default = entry["default"]
        if entry_type == "int":
            assert isinstance(default, int) and not isinstance(default, bool), (
                f"{key} declares type=int but default={default!r}"
            )
        elif entry_type == "float":
            assert isinstance(default, (int, float)), (
                f"{key} declares type=float but default={default!r}"
            )
        elif entry_type == "bool":
            # bools are stored as the strings "true"/"false" (env vars are
            # always strings at runtime) — see LOG_TOOL_TRACE etc.
            assert str(default).lower() in ("true", "false"), (
                f"{key} declares type=bool but default={default!r}"
            )
        elif entry_type == "string":
            assert isinstance(default, str), f"{key} declares type=string but default={default!r}"


def test_range_only_present_for_numeric_types_and_well_formed(config):
    for key, entry in config["env"].items():
        range_ = entry.get("range")
        if range_ is None:
            continue
        assert entry.get("type") in ("int", "float"), (
            f"{key} has range but type={entry.get('type')}"
        )
        assert len(range_) == 2, f"{key} range must be [low, high]"
        low, high = range_
        assert low <= high, f"{key} range low > high: {range_}"
        assert low <= entry["default"] <= high, (
            f"{key} default {entry['default']} is outside its own range {range_}"
        )


def test_model_override_is_bool(config):
    for key, entry in config["env"].items():
        assert isinstance(entry["model_override"], bool), f"{key} model_override must be a bool"


def test_no_duplicate_env_var_names_case_insensitive(config):
    seen = {}
    for key in config["env"]:
        lowered = key.lower()
        assert lowered not in seen, (
            f"duplicate env var (case-insensitive): {key} vs {seen.get(lowered)}"
        )
        seen[lowered] = key


def test_tool_params_section_present_and_well_formed(config):
    assert "tool_params" in config
    for tool_name, params in config["tool_params"].items():
        assert isinstance(params, dict), f"tool_params.{tool_name} must be a table"
        for param_name, entry in params.items():
            assert "default" in entry, f"tool_params.{tool_name}.{param_name} missing 'default'"


def test_vector_search_top_k_documented(config):
    """The spec calls out vector_search.top_k explicitly — guard against
    accidental removal."""
    assert "vector_search" in config["tool_params"]
    assert "top_k" in config["tool_params"]["vector_search"]
    assert config["tool_params"]["vector_search"]["top_k"]["default"] == 10
    assert config["tool_params"]["vector_search"]["top_k"]["max"] == 25


@pytest.mark.parametrize(
    "expected_var",
    [
        "STATUTE_BACKFILL_SOURCE_GATE",
        "STATUTE_BACKFILL_CAP",
        "CASELAW_BACKFILL_CAP",
        "CASELAW_CHUNK_FETCH_K",
        "CASELAW_CHUNK_HARD_CAP",
        "CASELAW_CHUNK_MAX_PER_CASE",
        "BROAD_DISCOVERY_CAP",
        "DIVERSITY_CAP_PER_DOC",
        "ENRICH_CAP_PER_DOC",
        "ENRICH_CAP_PER_TYPE",
        "FAQ_KNOWLEDGE_BASE_ID",
        "FAQ_SCORE_THRESHOLD",
        "MAX_TURNS",
        "AGENTIC_MODEL_ID",
        "REFINEMENT_MODEL_ID",
        "CASE_LAW_SUMMARY_MODEL",
        "RAW_BUCKET",
        "LOG_TOOL_TRACE",
        "LOG_QUERY_TEXT",
        "LOG_MAX_TEXT_CHARS",
        "WEBSOCKET_CALLBACK_URL",
        "SESSIONS_TABLE_NAME",
        "MODEL_CONFIG_TABLE_NAME",
        "NEPTUNE_GRAPH_ID",
    ],
)
def test_spec_documented_env_vars_present(config, expected_var):
    """Every env var listed in the spec's table must exist in the TOML."""
    assert expected_var in config["env"], f"{expected_var} missing from config/retrieval.toml"
