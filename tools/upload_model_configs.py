#!/usr/bin/env python3
"""
Upload model configurations from config/model_configs.toml to DynamoDB.

Usage:
  # Upload all configs (auto-discovers table name from CDK stack):
  AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/upload_model_configs.py

  # Upload with explicit table name:
  AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/upload_model_configs.py --table-name MyTable

  # Dry run (validate only, no upload):
  python tools/upload_model_configs.py --dry-run

  # Upload a single config entry:
  AWS_PROFILE=widor AWS_REGION=us-east-1 python tools/upload_model_configs.py --only agenticRetrieval
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

import boto3
import toml

script_dir = Path(__file__).parent
repo_root = script_dir.parent
bedrock_utils_dir = repo_root / "tools"
sys.path.insert(0, str(bedrock_utils_dir))

step_function_types_dir = repo_root / "backend" / "layers"
sys.path.insert(0, str(step_function_types_dir))

from bedrock_utils import BedrockConfig, InferenceConfig, ModelConfig, SystemPrompt  # noqa: E402


def get_aws_region() -> str:
    region = os.environ.get("AWS_REGION") or boto3.Session().region_name
    if not region:
        raise RuntimeError("No AWS region configured. Set AWS_REGION env var.")
    return region


def get_default_table_name(region: str | None = None) -> str:
    if not region:
        region = get_aws_region()
    cf_client = boto3.client("cloudformation", region_name=region)
    response = cf_client.describe_stacks(StackName="WisconsinBotGraphRAG")
    stack = response["Stacks"][0]
    for output in stack.get("Outputs", []):
        if output["OutputKey"] == "ModelConfigTableName":
            return output["OutputValue"]
    raise RuntimeError("ModelConfigTableName output not found in WisconsinBotGraphRAG stack")


def parse_toml_config(config_file: str) -> dict[str, ModelConfig]:
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with open(config_file) as f:
        toml_data = toml.load(f)

    configs = {}
    for config_id, config_data in toml_data.items():
        model_config_data = {
            "id": config_data.get("id", config_id),
            "prompt": config_data.get("prompt"),
        }

        if "config" in config_data:
            bedrock_config_data = config_data["config"]
            system_prompts = None
            if "system" in bedrock_config_data:
                system_prompts = [
                    SystemPrompt(**prompt_data) for prompt_data in bedrock_config_data["system"]
                ]

            inference_config = None
            if "inferenceConfig" in bedrock_config_data:
                inference_config = InferenceConfig(**bedrock_config_data["inferenceConfig"])

            additional_fields = {}
            excluded_keys = {"modelId", "system", "inferenceConfig"}
            for key, value in bedrock_config_data.items():
                if key not in excluded_keys:
                    additional_fields[key] = value

            bedrock_config = BedrockConfig(
                modelId=bedrock_config_data["modelId"],
                system=system_prompts,
                inferenceConfig=inference_config,
                additionalModelRequestFields=additional_fields if additional_fields else None,
            )
            model_config_data["config"] = bedrock_config

        model_config = ModelConfig(**model_config_data)
        configs[config_id] = model_config

    return configs


def convert_floats_to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    return obj


def upload_to_dynamodb(
    configs: dict[str, ModelConfig], table_name: str, region: str | None = None
):
    if not region:
        region = get_aws_region()

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    print(f"Uploading {len(configs)} configuration(s) to table: {table_name}")

    for config_id, model_config in configs.items():
        item = model_config.model_dump(by_alias=True, exclude_none=True)
        item = convert_floats_to_decimal(item)
        table.put_item(Item=item)
        print(f"  ✓ {config_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Upload model configurations from TOML to DynamoDB"
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default=str(repo_root / "config" / "model_configs.toml"),
        help="Path to TOML config file (default: config/model_configs.toml)",
    )
    parser.add_argument("--table-name", help="DynamoDB table name (default: from CDK stack)")
    parser.add_argument("--region", help="AWS region (default: AWS_REGION env var)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without uploading")
    parser.add_argument("--only", help="Upload only this config ID (e.g. agenticRetrieval)")

    args = parser.parse_args()

    try:
        print(f"Parsing: {args.config_file}")
        configs = parse_toml_config(args.config_file)
        print(f"Found {len(configs)} configuration(s):")
        for config_id, config in configs.items():
            print(f"  - {config_id} (model: {config.config.modelId if config.config else 'none'})")

        if args.only:
            if args.only not in configs:
                print(f"✗ Config '{args.only}' not found in TOML. Available: {list(configs.keys())}")
                sys.exit(1)
            configs = {args.only: configs[args.only]}

        if args.dry_run:
            print("Dry run — not uploading.")
            return

        region = args.region or get_aws_region()
        table_name = args.table_name or get_default_table_name(region)
        upload_to_dynamodb(configs, table_name, region)
        print("✓ Done")

    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
