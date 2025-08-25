#!/usr/bin/env python3
"""
Script to parse TOML configuration files and upload model configurations to DynamoDB.
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import toml
from botocore.exceptions import ClientError

# For bedrock_utils
script_dir = Path(__file__).parent
bedrock_utils_dir = script_dir.parent / "packages" / "messages" / "lambdas" / "streaming"
sys.path.insert(0, str(bedrock_utils_dir))

step_function_types_dir = script_dir.parent / "packages" / "shared" / "lambda_layers"
sys.path.insert(0, str(step_function_types_dir))

from bedrock_utils import BedrockConfig, InferenceConfig, ModelConfig, SystemPrompt  # noqa: E402


def parse_toml_config(config_file: str) -> dict[str, ModelConfig]:
    """
    Parse TOML configuration file and convert to ModelConfig objects.

    Args:
        config_file: Path to the TOML configuration file

    Returns:
        Dictionary mapping config IDs to ModelConfig objects

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If configuration is invalid
    """
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    # Load TOML file
    with open(config_file) as f:
        toml_data = toml.load(f)

    configs = {}

    for config_id, config_data in toml_data.items():
        try:
            # Extract basic fields
            model_config_data = {
                "id": config_data.get("id", config_id),
                "prompt": config_data.get("prompt"),
            }

            # Process Bedrock config if present
            if "config" in config_data:
                bedrock_config_data = config_data["config"]

                # Process system prompts
                system_prompts = None
                if "system" in bedrock_config_data:
                    system_prompts = [
                        SystemPrompt(**prompt_data) for prompt_data in bedrock_config_data["system"]
                    ]

                # Process inference config
                inference_config = None
                if "inferenceConfig" in bedrock_config_data:
                    inference_config = InferenceConfig(**bedrock_config_data["inferenceConfig"])

                # Process additional model request fields (anything not in the main config)
                additional_fields = {}
                excluded_keys = {"modelId", "system", "inferenceConfig"}
                for key, value in bedrock_config_data.items():
                    if key not in excluded_keys:
                        additional_fields[key] = value

                # Create BedrockConfig
                bedrock_config = BedrockConfig(
                    modelId=bedrock_config_data["modelId"],
                    system=system_prompts,
                    inferenceConfig=inference_config,
                    additionalModelRequestFields=additional_fields if additional_fields else None,
                )

                model_config_data["config"] = bedrock_config

            # Create and validate ModelConfig
            model_config = ModelConfig(**model_config_data)
            configs[config_id] = model_config

        except Exception as e:
            raise ValueError(f"Invalid configuration for {config_id}: {e}") from e

    return configs


def convert_floats_to_decimal(obj):
    """
    Recursively convert float values to Decimal for DynamoDB compatibility.

    Args:
        obj: Object that may contain float values

    Returns:
        Object with floats converted to Decimal
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    else:
        return obj


def upload_to_dynamodb(configs: dict[str, ModelConfig], table_name: str, region: str | None = None):
    """
    Upload model configurations to DynamoDB.

    Args:
        configs: Dictionary of ModelConfig objects keyed by ID
        table_name: Name of the DynamoDB table
        region: AWS region (optional, uses boto3 default if not provided)

    Raises:
        ClientError: If DynamoDB operations fail
    """
    if region:
        dynamodb = boto3.resource("dynamodb", region_name=region)
    else:
        dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    print(f"Uploading {len(configs)} configurations to DynamoDB table: {table_name}")

    for config_id, model_config in configs.items():
        try:
            # Convert ModelConfig to dict using pydantic's model_dump
            item = model_config.model_dump(by_alias=True, exclude_none=True)

            # Convert floats to Decimal for DynamoDB compatibility
            item = convert_floats_to_decimal(item)

            # Put item in DynamoDB
            table.put_item(Item=item)
            print(f"✓ Uploaded configuration: {config_id}")

        except ClientError as e:
            print(f"✗ Failed to upload {config_id}: {e}")
            raise
        except Exception as e:
            print(f"✗ Error processing {config_id}: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(
        description="Upload model configurations from TOML file to DynamoDB"
    )
    parser.add_argument("config_file", help="Path to the TOML configuration file")
    parser.add_argument("--table-name", required=True, help="Name of the DynamoDB table")
    parser.add_argument(
        "--region", help="AWS region (optional, uses boto3 default if not provided)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate configs without uploading to DynamoDB",
    )

    args = parser.parse_args()

    try:
        # Parse configuration file
        print(f"Parsing configuration file: {args.config_file}")
        configs = parse_toml_config(args.config_file)
        print(f"Successfully parsed {len(configs)} configurations")

        # Validate configurations
        for config_id, config in configs.items():
            print(
                f"  - {config_id}: {config.id} (model: {config.config.modelId if config.config else 'none'})"
            )

        if args.dry_run:
            print("Dry run mode - not uploading to DynamoDB")
            return

        # Upload to DynamoDB
        upload_to_dynamodb(configs, args.table_name, args.region)
        print("✓ All configurations uploaded successfully")

    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
