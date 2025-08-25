import os
from typing import Any

import boto3
import pydantic
from boto3.dynamodb.types import TypeDeserializer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from step_function_types.errors import ConfigNotFound

# PyDantic models that validate Bedrock model configuration parameters.


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
        return self.dict(exclude_none=True)


def to_camel(string: str) -> str:
    """Convert snake_case string to camelCase."""
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:]) if len(parts) > 1 else string


class ModelConfig(BaseModel):
    config: BedrockConfig | None = Field(
        None, description="Configuration for Bedrock models", alias="config"
    )
    id: str = Field(..., min_length=1, description="Model identifier", alias="id")
    prompt: str | None = Field(
        None, min_length=1, description="Prompt for the model", alias="prompt"
    )
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
