#!/usr/bin/env python3
"""
Script to parse TOML configuration files and upload model configurations to DynamoDB.
"""

import argparse
import json
import os
import subprocess
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


def get_aws_region() -> str:
    """
    Get the AWS region from the current session configuration.
    
    Returns:
        AWS region string
        
    Raises:
        RuntimeError: If region cannot be determined
    """
    try:
        session = boto3.Session()
        region = session.region_name
        if not region:
            raise RuntimeError("No AWS region configured")
        return region
    except Exception as e:
        raise RuntimeError(f"Failed to get AWS region: {e}")


def get_stack_output(stack_name: str, output_key: str, region: str | None = None) -> str:
    """
    Get a specific output value from a CloudFormation stack.
    
    Args:
        stack_name: Name of the CloudFormation stack
        output_key: Key of the output to retrieve
        region: AWS region (optional)
        
    Returns:
        Output value as string
        
    Raises:
        RuntimeError: If stack or output not found
    """
    try:
        if not region:
            region = get_aws_region()
            
        cf_client = boto3.client("cloudformation", region_name=region)
            
        response = cf_client.describe_stacks(StackName=stack_name)
        
        if not response["Stacks"]:
            raise RuntimeError(f"Stack {stack_name} not found")
            
        stack = response["Stacks"][0]
        outputs = stack.get("Outputs", [])
        
        for output in outputs:
            if output["OutputKey"] == output_key:
                return output["OutputValue"]
                
        raise RuntimeError(f"Output {output_key} not found in stack {stack_name}")
        
    except ClientError as e:
        raise RuntimeError(f"Failed to get stack output: {e}")


def get_default_table_name(region: str | None = None) -> str:
    """
    Get the model config table name from the CDK stack.
    
    Args:
        region: AWS region (optional)
        
    Returns:
        Table name
    """
    return get_stack_output("WisconsinBotStack", "ModelConfigTableName", region)


def parse_toml_config(config_file: str) -> tuple[dict[str, ModelConfig], dict]:
    """
    Parse TOML configuration file and convert to ModelConfig objects.

    Args:
        config_file: Path to the TOML configuration file

    Returns:
        Tuple of (model configs dictionary, retrieval config dictionary)

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
    retrieval_config = {}

    for config_id, config_data in toml_data.items():
        # Handle retrievalConfig separately
        if config_id == "retrievalConfig":
            retrieval_config = config_data
            continue

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

    return configs, retrieval_config


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


def upload_to_dynamodb(configs: dict[str, ModelConfig], retrieval_config: dict, table_name: str, region: str | None = None):
    """
    Upload model configurations to DynamoDB.

    Args:
        configs: Dictionary of ModelConfig objects keyed by ID
        retrieval_config: Retrieval configuration dictionary
        table_name: Name of the DynamoDB table
        region: AWS region (optional, uses session default if not provided)

    Raises:
        ClientError: If DynamoDB operations fail
    """
    if not region:
        region = get_aws_region()

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    total_configs = len(configs) + (1 if retrieval_config else 0)
    print(f"Uploading {total_configs} configurations to DynamoDB table: {table_name}")

    # Upload model configs
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

    # Upload retrieval config
    if retrieval_config:
        try:
            item = {"id": "retrievalConfig", **retrieval_config}
            # Convert floats to Decimal for DynamoDB compatibility
            item = convert_floats_to_decimal(item)
            table.put_item(Item=item)
            print(f"✓ Uploaded configuration: retrievalConfig")
        except ClientError as e:
            print(f"✗ Failed to upload retrievalConfig: {e}")
            raise
        except Exception as e:
            print(f"✗ Error processing retrievalConfig: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(
        description="Upload model configurations from TOML file to DynamoDB"
    )
    parser.add_argument(
        "config_file", 
        nargs="?",
        default="config/model_configs.toml",
        help="Path to the TOML configuration file (default: config/model_configs.toml)"
    )
    parser.add_argument(
        "--table-name", 
        help="Name of the DynamoDB table (default: derived from CDK stack)"
    )
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
        configs, retrieval_config = parse_toml_config(args.config_file)
        print(f"Successfully parsed {len(configs)} model configurations")

        # Validate configurations
        for config_id, config in configs.items():
            print(
                f"  - {config_id}: {config.id} (model: {config.config.modelId if config.config else 'none'})"
            )

        if args.dry_run:
            print("Dry run mode - not uploading to DynamoDB")
            return

        # Get table name
        table_name = args.table_name
        if not table_name:
            print("Getting table name from CDK stack...")
            table_name = get_default_table_name(args.region)
            print(f"Using table: {table_name}")

        # Upload to DynamoDB
        upload_to_dynamodb(configs, retrieval_config, table_name, args.region)
        print("✓ All configurations uploaded successfully")

    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
