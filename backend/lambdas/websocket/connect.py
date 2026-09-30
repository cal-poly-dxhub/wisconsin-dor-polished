import logging
import os
import time
from datetime import datetime

import boto3
import jwt
from botocore.exceptions import ClientError
from response_utils import create_error_response, create_websocket_response
from validators import validate_connect_event
from websocket_errors import SessionNotFound, Unauthorized, WebSocketError, create_error_body

logger = logging.getLogger()
logger.setLevel(logging._nameToLevel.get(os.environ.get("LOG_LEVEL", "INFO"), logging.INFO))

# Initialize DynamoDB client
dynamodb = boto3.client("dynamodb")
table_name = os.environ.get("SESSIONS_TABLE_NAME")
user_pool_id = os.environ.get("USER_POOL_ID", "")
user_pool_client_id = os.environ.get("USER_POOL_CLIENT_ID", "")
_region = user_pool_id.split("_", 1)[0] if user_pool_id else os.environ.get("AWS_REGION", "")
_issuer = f"https://cognito-idp.{_region}.amazonaws.com/{user_pool_id}"
# Module-level so the signing keys are fetched once per container and cached.
_jwks = jwt.PyJWKClient(f"{_issuer}/.well-known/jwks.json", cache_keys=True)


def verify_id_token(token: str | None) -> str:
    """Return the ``sub`` of a valid Cognito ID token for this app client.

    Checks the RS256 signature against the pool's JWKS, expiry, issuer,
    audience (the app client id) and ``token_use == "id"`` (the same token
    the HTTP API's JWT authorizer accepts).
    """
    if not token:
        raise Unauthorized("missing token")
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=user_pool_client_id,
            issuer=_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise Unauthorized(f"invalid token: {type(e).__name__}") from e
    if claims.get("token_use") != "id":
        raise Unauthorized("not an ID token")
    return claims["sub"]


def record_session_data(session_id: str, connection_id: str, user_id: str):
    """Bind the connection to the session, only if the caller owns it."""
    try:
        dynamodb.update_item(
            TableName=table_name,
            Key={"sessionId": {"S": session_id}},
            UpdateExpression="SET connectionId = :cid, #ts = :ts, #ttl = :ttl",
            ExpressionAttributeNames={"#ts": "timestamp", "#ttl": "ttl"},
            ExpressionAttributeValues={
                ":uid": {"S": user_id},
                ":cid": {"S": connection_id},
                ":ts": {"S": datetime.now().isoformat()},
                ":ttl": {"N": str(int(time.time()) + 7200)},
            },
            # Someone else's session (or a legacy one with no owner) fails the
            # same way as a missing one, so a leaked session id reveals nothing.
            ConditionExpression="attribute_exists(sessionId) AND userId = :uid",
        )
        logger.info(
            f"Successfully updated session {session_id} with connection ID: {connection_id}"
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"DynamoDB error while updating session {session_id}: {error_code}")

        if error_code == "ConditionalCheckFailedException":
            raise SessionNotFound(session_id) from e
        else:
            raise e


def handler(event, context):
    """
    Handle WebSocket connect events
    """

    try:
        # The query string carries the user's ID token: never log the raw event.
        validated_event = validate_connect_event(event)

        connection_id = validated_event.requestContext.connectionId
        session_id = validated_event.queryStringParameters.sessionId
        logger.info(f"Connect request for session {session_id} (connection {connection_id})")

        user_id = verify_id_token(validated_event.queryStringParameters.token)
        record_session_data(session_id, connection_id, user_id)

        logger.info(
            f"Connection established for session {session_id} with connection ID: {connection_id}"
        )
        return create_websocket_response(200, {"message": "Connected"})

    except WebSocketError as e:
        logger.error(f"Error while processing connect event: {e}")
        return create_error_response(e)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        body = create_error_body(e)
        return create_websocket_response(500, body)
