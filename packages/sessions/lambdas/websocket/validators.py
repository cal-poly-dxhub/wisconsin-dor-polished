import logging
import os
from typing import Any, Literal

import pydantic
from pydantic import BaseModel, ConfigDict, Field
from websocket_errors import ValidationError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))


class RequestContext(BaseModel):
    connectionId: str
    domainName: str
    stage: str
    eventType: Literal["CONNECT", "DISCONNECT", "MESSAGE"]

    model_config = ConfigDict(extra="ignore")


class QueryStringParameters(BaseModel):
    """Query string parameters for websocket events"""

    sessionId: str = Field(description="Session ID for the websocket connection")
    model_config = ConfigDict(extra="ignore")


class MessageBody(BaseModel):
    """
    Body of a message event. Used during testing for messages that
    should be echoed to the client.
    """

    message: str = Field(..., description="The message to be echoed back")
    model_config = ConfigDict(extra="ignore")


class ConnectEvent(BaseModel):
    """WebSocket connect event with required sessionId"""

    requestContext: RequestContext
    queryStringParameters: QueryStringParameters

    model_config = ConfigDict(extra="ignore")

    def model_post_init(self, __context) -> None:
        """Validate sessionId is required for CONNECT events"""
        if not self.queryStringParameters or not self.queryStringParameters.sessionId:
            raise pydantic.ValidationError("sessionId is required for CONNECT events")


class DisconnectEvent(BaseModel):
    """WebSocket disconnect event - no additional requirements"""

    requestContext: RequestContext

    model_config = ConfigDict(extra="ignore")


class MessageEvent(BaseModel):
    """WebSocket message event with parsed message body"""

    requestContext: RequestContext
    body: str

    model_config = ConfigDict(extra="ignore")


def validate_connect_event(event: dict[str, Any]) -> ConnectEvent:
    """Validate a websocket connect event"""
    try:
        return ConnectEvent.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Connect event validation failed: {str(e)}")
        raise ValidationError() from e


def validate_disconnect_event(event: dict[str, Any]) -> DisconnectEvent:
    """Validate a websocket disconnect event"""
    try:
        return DisconnectEvent.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Disconnect event validation failed: {str(e)}")
        raise ValidationError() from e


def validate_message_event(event: dict[str, Any]) -> MessageEvent:
    """Validate a websocket message event"""
    try:
        return MessageEvent.model_validate(event)
    except pydantic.ValidationError as e:
        logger.error(f"Message event validation failed: {str(e)}")
        raise ValidationError() from e
