from pydantic import BaseModel


# Request body used to submit a message
class MessageRequest(BaseModel):
    message: str


# Event emitted over EventBridge to trigger step function
class MessageEvent(BaseModel):
    query: str
    query_id: str
    session_id: str
