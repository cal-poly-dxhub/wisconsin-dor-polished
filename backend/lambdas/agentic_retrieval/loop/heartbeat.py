"""WebSocket keepalive heartbeat, shared by Phase A and Phase B.

API Gateway idle-times-out WebSocket connections; long Bedrock calls can
exceed that window, so a daemon thread pings the connection until stopped.
"""

import json
import logging
import threading

import config

logger = logging.getLogger(__name__)


def start_heartbeat(
    ws_server,
    connection_alive: list[bool],
    *,
    label: str = "heartbeat",
) -> threading.Event:
    """Start a keepalive ping thread; returns the Event that stops it.

    ``connection_alive`` is a single-element list used as a mutable flag
    shared with the caller — the thread flips it to False when the
    connection is gone. No thread is started when the server is missing or
    the connection is already dead; the returned Event is still valid to set.
    """
    stop = threading.Event()

    def _loop():
        while not stop.wait(config.WS_HEARTBEAT_INTERVAL):
            if not ws_server or not connection_alive[0]:
                break
            try:
                ws_server.client.post_to_connection(
                    ConnectionId=ws_server.connection_id,
                    Data=json.dumps({"streamId": "heartbeat", "body": {}}),
                )
            except Exception:
                logger.info(f"WebSocket connection gone during {label}")
                connection_alive[0] = False
                break

    thread = threading.Thread(target=_loop, daemon=True)
    if ws_server and connection_alive[0]:
        thread.start()
    return stop
