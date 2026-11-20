"""Single WebSocket endpoint; domain subscriptions share a transport-independent hub."""

from fastapi import APIRouter, WebSocket

from app.adapters.websocket_adapter import serve_websocket
from app.core.config import get_settings

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def realtime_socket(socket: WebSocket) -> None:
    """Resolve application-owned resources and delegate the socket lifecycle."""
    hub = getattr(socket.app.state, "realtime", None)
    if hub is None:
        await socket.close(code=1013)
        return
    await serve_websocket(socket, hub, get_settings())
