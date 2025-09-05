import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from step_function_types.errors import report_error, GenericStreamingError, ThrottlingError

import boto3
import pydantic
from boto3.dynamodb.types import TypeDeserializer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from step_function_types.errors import ConfigNotFound

# PyDantic models that validate Bedrock model configuration parameters.

logger = logging.getLogger(__name__)


class SystemPrompt(BaseModel):
    text: str = Field(..., min_length=1, description="System prompt text")


class InferenceConfig(BaseModel):
    maxTokens: int | None = Field(
        None,
        ge=1,
        le=4096,
        description="Maximum tokens to generate",
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Temperature for randomness",
    )
    topP: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top-p sampling parameter",
    )
    stopSequences: list[str] | None = Field(
        None,
        description="Stop sequences for generation",
    )

    @field_validator("maxTokens")
    @classmethod
    def validate_max_tokens(cls, v):
        if v is not None and v <= 0:
            raise ValueError("maxTokens must be positive")
        return v


class AdditionalModelRequestFields(BaseModel):
    model_config = ConfigDict(extra="allow")


class BedrockConfig(BaseModel):
    modelId: str = Field(..., min_length=1, description="Bedrock model identifier")
    system: list[SystemPrompt] | None = Field(None, description="System prompts")
    inferenceConfig: InferenceConfig | None = Field(None, description="Inference configuration")
    additionalModelRequestFields: AdditionalModelRequestFields | None = Field(
        None, description="Model-specific fields"
    )

    @field_validator("modelId")
    @classmethod
    def validate_model_id(cls, v):
        if not v.strip():
            raise ValueError("modelId cannot be empty")
        return v

    def to_bedrock_params(self) -> dict[str, Any]:
        """Convert to parameters ready for bedrock.converse() call"""
        return self.model_dump(exclude_none=True)


def to_camel(string: str) -> str:
    """Convert snake_case string to camelCase."""
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:]) if len(parts) > 1 else string


class ModelConfig(BaseModel):
    config: BedrockConfig = Field(
        ..., description="Configuration for Bedrock models", alias="config"
    )
    id: str = Field(..., min_length=1, description="Model identifier", alias="id")
    prompt: str = Field(min_length=1, description="Prompt for the model", alias="prompt")
    model_config = ConfigDict(
        populate_by_name=True, allow_population_by_alias=True, alias_generator=to_camel
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Prompt cannot be empty")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not v.strip():
            raise ValueError("Model ID cannot be empty")
        return v


def get_model_config_from_dynamo(config_id: str) -> ModelConfig:
    MODEL_CONFIG_TABLE_NAME = os.environ.get("MODEL_CONFIG_TABLE_NAME")

    client = boto3.client("dynamodb")
    response = client.get_item(TableName=MODEL_CONFIG_TABLE_NAME, Key={"id": {"S": config_id}})

    item = response.get("Item")
    if not item:
        raise ConfigNotFound(details={"config_id": config_id})
    deserializer = TypeDeserializer()
    item_dict = {k: deserializer.deserialize(v) for k, v in item.items()}
    try:
        config = ModelConfig(**item_dict)
    except pydantic.ValidationError as e:
        raise ValueError(f"Invalid config for model ID {config_id}: {e}") from e
    return config


async def call_bedrock_converse(
    query: str, model_config: ModelConfig, region: str = "us-west-2"
) -> AsyncGenerator[str]:
    """
    Call Bedrock Converse API with streaming using ModelConfig.

    Args:
        query: The user's query/message
        model_config: The ModelConfig containing Bedrock configuration
        region: AWS region for Bedrock client (default: us-west-2)

    Yields:
        str: Text fragments from the streaming response
    """

    logger.info(f"Calling Bedrock Converse API with query: {query}")
    bedrock_client = boto3.client("bedrock-runtime", region_name=region)

    conversation_request: dict = {
        "messages": [{"role": "user", "content": [{"text": query}]}],
        **model_config.config.to_bedrock_params(),
    }

    try:
        response = bedrock_client.converse_stream(**conversation_request)
    except Exception as e:
        logger.error(f"Error calling Bedrock Converse API: {e}")
        raise GenericStreamingError(details={"original_error": str(e)}) from e

    logger.info("Starting to process Bedrock stream response")

    stream = response.get("stream")
    if not stream:
        logger.error("No stream found in Bedrock response")
        raise GenericStreamingError(details={"message": "No stream in Bedrock response"})

    for event in stream:
        # Handle contentBlockDelta events which contain text deltas
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                text_fragment = delta["text"]
                logger.debug(f"Yielding text fragment: {text_fragment}")
                yield text_fragment

        # Handle messageStart events
        elif "messageStart" in event:
            logger.debug(f"Message started with role: {event['messageStart'].get('role')}")

        # Handle contentBlockStart events
        elif "contentBlockStart" in event:
            logger.debug(
                f"Content block started at index: {event['contentBlockStart'].get('contentBlockIndex')}"
            )

        # Handle contentBlockStop events
        elif "contentBlockStop" in event:
            logger.debug(
                f"Content block stopped at index: {event['contentBlockStop'].get('contentBlockIndex')}"
            )

        # Handle messageStop events
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
            logger.info(f"Message stopped with reason: {stop_reason}")

        # Handle metadata events (usage, metrics, etc.)
        elif "metadata" in event:
            metadata = event["metadata"]
            if "usage" in metadata:
                usage = metadata["usage"]
                logger.info(
                    f"Token usage - Input: {usage.get('inputTokens')}, Output: {usage.get('outputTokens')}, Total: {usage.get('totalTokens')}"
                )

        elif (
            "internalServerException" in event
            or "modelStreamErrorException" in event
            or "validationException" in event
            or "serviceUnavailableException" in event
        ):
            logger.error(f"Error event from Bedrock: {event}")
            raise GenericStreamingError(details={"event": event})

        elif "throttlingException" in event:
            logger.error(f"Throttling event from Bedrock: {event}")
            raise ThrottlingError(details={"event": event})

        else:
            logger.error(f"Unknown event type from Bedrock: {event}")
            raise GenericStreamingError(details={"unknown_event": event})
