#!/usr/bin/env python3
"""
Upload model configurations from config/model_configs.toml to DynamoDB.

Usage:
  # Upload all configs (auto-discovers table name from CDK stack):
  AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 python tools/upload_model_configs.py

  # Upload with explicit table name:
  AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 python tools/upload_model_configs.py --table-name MyTable

  # Dry run (validate only, no upload):
  python tools/upload_model_configs.py --dry-run

  # Upload a single config entry:
  AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 python tools/upload_model_configs.py --only agenticRetrieval
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

import boto3

repo_root = Path(__file__).parent.parent


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


def parse_toml_config(config_file: str) -> dict[str, str]:
    """Return {config id: prompt}. The Lambda reads only the prompt text."""
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with open(config_file, "rb") as f:
        toml_data = tomllib.load(f)  # stdlib parser — same one the Lambda uses

    configs = {}
    for config_id, config_data in toml_data.items():
        entry_id = config_data.get("id", config_id)
        prompt = config_data.get("prompt") or ""
        if entry_id != config_id:
            raise ValueError(f"[{config_id}] has id = {entry_id!r}; they must match")
        if not prompt.strip():
            raise ValueError(f"[{config_id}] has an empty prompt")
        configs[config_id] = prompt
    return configs


def upload_to_dynamodb(configs: dict[str, str], table_name: str, region: str | None = None):
    if not region:
        region = get_aws_region()

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    print(f"Uploading {len(configs)} configuration(s) to table: {table_name}")

    for config_id, prompt in configs.items():
        table.put_item(Item={"id": config_id, "prompt": prompt})
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
        for config_id, prompt in configs.items():
            print(f"  - {config_id} ({len(prompt):,} chars)")

        if args.only:
            if args.only not in configs:
                print(
                    f"✗ Config '{args.only}' not found in TOML. Available: {list(configs.keys())}"
                )
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
