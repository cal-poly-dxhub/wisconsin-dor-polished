"""
Shared Pydantic models and error handling for chat message processing.

(The layer name is a holdover from the removed Step Function architecture;
the models themselves are the live inter-Lambda contracts.)
"""

# Errors
from .errors import (
    MessagesError,
    UnexpectedError,
    ValidationError,
    report_error,
)

# Models
from .models import (
    FAQResource,
    MessageEvent,
    MessageProcessingErrorResponse,
    MessageRequest,
    RAGDocument,
    UserQuery,
)

__all__ = [
    # Models
    "MessageRequest",
    "MessageEvent",
    "UserQuery",
    "FAQResource",
    "RAGDocument",
    "MessageProcessingErrorResponse",
    # Errors
    "MessagesError",
    "ValidationError",
    "UnexpectedError",
    "report_error",
]
