"""System prompt loader for the Wisconsin DOR agentic retrieval Lambda.

Loads the prompt from the ModelConfig DynamoDB table at cold-start and caches
it for the lifetime of the Lambda execution environment. This allows prompt
iteration without rebundling or redeploying the Lambda — just update DynamoDB.

Fallback: if MODEL_CONFIG_TABLE_NAME is not set (e.g. in tests), the prompt
is loaded from config/model_configs.toml at the repo root (or a bundled copy).
"""

import logging
import os

import boto3
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger(__name__)

_CONFIG_ID = "agenticRetrieval"


def _load_prompt_from_dynamo() -> str:
    table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")
    if not table_name:
        raise EnvironmentError("MODEL_CONFIG_TABLE_NAME not set")

    client = boto3.client("dynamodb")
    response = client.get_item(
        TableName=table_name,
        Key={"id": {"S": _CONFIG_ID}},
        ProjectionExpression="prompt",
    )
    item = response.get("Item")
    if not item:
        raise ValueError(f"No config found for id={_CONFIG_ID} in {table_name}")

    deserializer = TypeDeserializer()
    return deserializer.deserialize(item["prompt"])


def _load_prompt() -> str:
    try:
        return _load_prompt_from_dynamo()
    except Exception as e:
        logger.warning(f"Failed to load prompt from DynamoDB: {e}. Using bundled fallback.")
        from _prompt_fallback import SYSTEM_PROMPT_FALLBACK
        return SYSTEM_PROMPT_FALLBACK


SYSTEM_PROMPT: str = _load_prompt()
