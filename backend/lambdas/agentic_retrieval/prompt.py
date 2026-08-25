"""System prompt loader for the Wisconsin DOR agentic retrieval Lambda.

Loads prompts from the ModelConfig DynamoDB table at cold-start and caches
them for the lifetime of the Lambda execution environment. This allows prompt
iteration without rebundling or redeploying the Lambda — just update DynamoDB.

Fallback: if MODEL_CONFIG_TABLE_NAME is not set (e.g. in tests), prompts
are loaded from config/model_configs.toml at the repo root (or a bundled copy).
"""

import logging
import os

import boto3
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger(__name__)


def _load_prompt_from_dynamo(config_id: str) -> str:
    table_name = os.environ.get("MODEL_CONFIG_TABLE_NAME")
    if not table_name:
        raise OSError("MODEL_CONFIG_TABLE_NAME not set")

    client = boto3.client("dynamodb")
    response = client.get_item(
        TableName=table_name,
        Key={"id": {"S": config_id}},
        ProjectionExpression="prompt",
    )
    item = response.get("Item")
    if not item:
        raise ValueError(f"No config found for id={config_id} in {table_name}")

    deserializer = TypeDeserializer()
    return deserializer.deserialize(item["prompt"])


def _load_prompt(config_id: str, fallback_attr: str) -> str:
    try:
        return _load_prompt_from_dynamo(config_id)
    except Exception as e:
        logger.warning(
            f"Failed to load prompt '{config_id}' from DynamoDB: {e}. Using bundled fallback."
        )
        import _prompt_fallback

        return getattr(_prompt_fallback, fallback_attr)


SYSTEM_PROMPT: str = _load_prompt("agenticRetrieval", "SYSTEM_PROMPT_FALLBACK")
ANSWER_STREAM_SYSTEM_PROMPT: str = _load_prompt("answerStream", "ANSWER_STREAM_PROMPT_FALLBACK")
DISAMBIGUATION_CLASSIFIER_PROMPT: str = _load_prompt(
    "disambiguationClassifier", "DISAMBIGUATION_CLASSIFIER_FALLBACK"
)

PERSONA_PROMPTS: dict[str, str] = {}
for _persona_id, _fallback_attr in [
    ("personaGovernment", "PERSONA_GOVERNMENT_FALLBACK"),
    ("personaCitizen", "PERSONA_CITIZEN_FALLBACK"),
]:
    try:
        PERSONA_PROMPTS[_persona_id] = _load_prompt_from_dynamo(_persona_id)
    except Exception:
        logger.warning(f"Persona prompt '{_persona_id}' not in DynamoDB; using empty.")
        PERSONA_PROMPTS[_persona_id] = ""
