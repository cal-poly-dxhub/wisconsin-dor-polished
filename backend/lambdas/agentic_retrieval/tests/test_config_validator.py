"""Tests for config_validator.py (cold-start env var validation)."""

import textwrap

import pytest

_SAMPLE_TOML = textwrap.dedent(
    """
    [env.MAX_TURNS]
    default = 8
    type = "int"
    description = "Maximum agentic loop turns before forced prepare_answer"
    range = [3, 15]
    stage = "loop"
    model_override = false

    [env.FAQ_SCORE_THRESHOLD]
    default = 0.70
    type = "float"
    description = "Minimum FAQ score to trigger high-confidence FAQ steering"
    range = [0.0, 1.0]
    stage = "faq_seed"
    model_override = false

    [env.LOG_TOOL_TRACE]
    default = "true"
    type = "bool"
    description = "Emit granular CloudWatch log events for each tool stage"
    stage = "observability"
    model_override = false

    [env.RAW_BUCKET]
    default = ""
    type = "string"
    description = "S3 bucket holding raw case-law opinion text"
    stage = "fetch_case_opinion"
    model_override = false
    """
)


@pytest.fixture
def sample_toml(tmp_path):
    path = tmp_path / "retrieval.toml"
    path.write_text(_SAMPLE_TOML)
    return path


def test_validate_env_missing_toml_returns_not_found(tmp_path):
    from config_validator import validate_env

    report = validate_env(toml_path=tmp_path / "does-not-exist.toml")

    assert report.toml_found is False
    assert report.checked == 0
    assert report.ok


def test_validate_env_malformed_toml_returns_not_found(tmp_path):
    from config_validator import validate_env

    path = tmp_path / "retrieval.toml"
    path.write_text("this is not [valid toml")

    report = validate_env(toml_path=path)

    assert report.toml_found is False
    assert report.ok


def test_validate_env_absent_vars_are_not_checked(sample_toml):
    from config_validator import validate_env

    # None of the documented vars are actually set in the environment.
    report = validate_env(toml_path=sample_toml, environ={})

    assert report.toml_found is True
    assert report.checked == 0
    assert report.ok


def test_validate_env_valid_values_pass(sample_toml):
    from config_validator import validate_env

    report = validate_env(
        toml_path=sample_toml,
        environ={
            "MAX_TURNS": "10",
            "FAQ_SCORE_THRESHOLD": "0.8",
            "LOG_TOOL_TRACE": "false",
            "RAW_BUCKET": "my-bucket",
        },
    )

    assert report.toml_found is True
    assert report.checked == 4
    assert report.ok


def test_validate_env_int_out_of_range(sample_toml):
    from config_validator import validate_env

    report = validate_env(toml_path=sample_toml, environ={"MAX_TURNS": "50"})

    assert not report.ok
    assert len(report.issues) == 1
    assert report.issues[0].env_var == "MAX_TURNS"
    assert "range" in report.issues[0].problem


def test_validate_env_int_wrong_type(sample_toml):
    from config_validator import validate_env

    report = validate_env(toml_path=sample_toml, environ={"MAX_TURNS": "not-a-number"})

    assert not report.ok
    assert report.issues[0].env_var == "MAX_TURNS"
    assert "expected int" in report.issues[0].problem


def test_validate_env_float_out_of_range(sample_toml):
    from config_validator import validate_env

    report = validate_env(toml_path=sample_toml, environ={"FAQ_SCORE_THRESHOLD": "1.5"})

    assert not report.ok
    assert report.issues[0].env_var == "FAQ_SCORE_THRESHOLD"


def test_validate_env_bool_invalid_value(sample_toml):
    from config_validator import validate_env

    report = validate_env(toml_path=sample_toml, environ={"LOG_TOOL_TRACE": "yes"})

    assert not report.ok
    assert report.issues[0].env_var == "LOG_TOOL_TRACE"
    assert "bool" in report.issues[0].problem


def test_validate_env_string_type_accepts_anything(sample_toml):
    from config_validator import validate_env

    report = validate_env(toml_path=sample_toml, environ={"RAW_BUCKET": "anything-goes-here"})

    assert report.ok


def test_validate_env_multiple_issues_all_reported(sample_toml):
    from config_validator import validate_env

    report = validate_env(
        toml_path=sample_toml,
        environ={"MAX_TURNS": "999", "LOG_TOOL_TRACE": "maybe"},
    )

    assert len(report.issues) == 2
    reported_vars = {issue.env_var for issue in report.issues}
    assert reported_vars == {"MAX_TURNS", "LOG_TOOL_TRACE"}


def test_validate_env_and_log_never_raises_on_missing_toml(tmp_path, caplog):
    from config_validator import validate_env_and_log

    report = validate_env_and_log(toml_path=tmp_path / "missing.toml")

    assert report.toml_found is False


def test_validate_env_and_log_never_raises_on_bad_values(sample_toml, caplog):
    from config_validator import validate_env_and_log

    # Should log a warning, not raise.
    report = validate_env_and_log(toml_path=sample_toml, environ={"MAX_TURNS": "999"})

    assert not report.ok
