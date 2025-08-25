"""
PyTest tests for Model Configuration Integration.

This test suite validates that the required model configurations
exist in DynamoDB and have the correct structure.
"""

import os
import sys

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "lambdas", "streaming"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "lambda_layers"))

from bedrock_utils import ModelConfig, get_model_config_from_dynamo
from step_function_types.errors import ConfigNotFound


def test_rag_response_config_exists_and_valid():
    """Test that ragResponse config exists and has valid structure."""
    model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")

    print(f"MODEL_CONFIG_TABLE_NAME: {model_config_table_name}")

    if model_config_table_name is None:
        pytest.fail("MODEL_CONFIG_TABLE_NAME environment variable is required")

    try:
        config = get_model_config_from_dynamo("ragResponse")

        assert isinstance(config, ModelConfig)
        assert config.id == "ragResponse"
        assert config.prompt is not None
        assert len(config.prompt) > 0

        # Verify prompt contains required placeholders
        assert "{documents}" in config.prompt
        assert "{query}" in config.prompt

        print("✓ ragResponse config validation passed")

    except ConfigNotFound as e:
        model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME", "unknown")
        pytest.fail(
            f"ragResponse config not found in DynamoDB table '{model_config_table_name}'. "
            "Please upload the model configurations using: "
            f"python scripts/upload_model_configs.py config/model_configs.toml --table-name {model_config_table_name}"
        )
    except Exception as e:
        pytest.fail(f"Failed to get ragResponse config: {e}")


def test_faq_response_config_exists_and_valid():
    """Test that faqResponse config exists and has valid structure."""
    model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")

    print(f"MODEL_CONFIG_TABLE_NAME: {model_config_table_name}")

    if not model_config_table_name:
        pytest.fail("MODEL_CONFIG_TABLE_NAME environment variable is required")

    try:
        config = get_model_config_from_dynamo("faqResponse")

        assert isinstance(config, ModelConfig)
        assert config.id == "faqResponse"
        assert config.prompt is not None
        assert len(config.prompt) > 0

        # Verify prompt contains required placeholders for FAQ
        assert "{query}" in config.prompt
        # FAQ config should contain at least one of these placeholders
        has_faq_placeholder = (
            "{faq}" in config.prompt or "{question}" in config.prompt or "{answer}" in config.prompt
        )
        assert has_faq_placeholder, "FAQ config must contain at least one FAQ-related placeholder"

        print("✓ faqResponse config validation passed")

    except ConfigNotFound as e:
        model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME", "unknown")
        pytest.fail(
            f"faqResponse config not found in DynamoDB table '{model_config_table_name}'. "
            "Please upload the model configurations using: "
            f"python scripts/upload_model_configs.py config/model_configs.toml --table-name {model_config_table_name}"
        )
    except Exception as e:
        pytest.fail(f"Failed to get faqResponse config: {e}")


def test_get_model_config_from_dynamo_with_invalid_config():
    """Test the get_model_config_from_dynamo function with non-existent config."""
    model_config_table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")

    if not model_config_table_name:
        pytest.fail("MODEL_CONFIG_TABLE_NAME environment variable is required")

    with pytest.raises(ConfigNotFound):
        get_model_config_from_dynamo("nonExistentConfig")
