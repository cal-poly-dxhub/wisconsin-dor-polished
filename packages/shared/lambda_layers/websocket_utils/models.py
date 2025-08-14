from pydantic import BaseModel, Field


class WebSocketMessage(BaseModel):
    pass


class PlainWebSocketMessage(WebSocketMessage):
    """
    A plain text message.
    """

    message: str = Field(description="The message to be sent over the websocket.")


class ErrorMessage(WebSocketMessage):
    """
    Used to report errors that occur during step function processing.
    """

    error: str = Field(description="The error message to be sent over the websocket.")
