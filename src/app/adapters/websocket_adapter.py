"""FastAPI transport adapter; one writer per socket and no database session per connection."""

import asyncio
import contextlib
import secrets

import anyio
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.realtime import ClientMessage, RealtimeEvent
from app.services.realtime_service import RealtimeService

logger = get_logger(__name__)


class AuthenticateMessage(BaseModel):
    """Browser clients send credentials in their first frame, never a URL query string."""

    model_config = ConfigDict(extra="forbid")
    type: str
    token: str = Field(max_length=4096)


async def serve_websocket(socket: WebSocket, hub: RealtimeService, settings: Settings) -> None:
    """Authenticate, enforce bounded input/idle limits, and release tasks on every exit."""
    origin = socket.headers.get("origin")
    if origin and origin not in settings.websocket_allowed_origins:
        await socket.close(code=1008)
        return
    await socket.accept()
    connection = None
    tasks: list[asyncio.Task] = []
    close_code = 1000

    async def receive(timeout: float) -> str:
        raw = await asyncio.wait_for(socket.receive_text(), timeout)
        if len(raw.encode("utf-8")) > settings.websocket_max_message_bytes:
            raise ValueError("frame_too_large")
        return raw

    try:
        principal = "development"
        if settings.admin_api_key or not settings.is_development:
            token = socket.headers.get("x-admin-key")
            if token is None:
                auth = AuthenticateMessage.model_validate_json(await receive(10))
                if auth.type != "authenticate":
                    raise ValueError("authentication_required")
                token = auth.token
            if not settings.admin_api_key or not secrets.compare_digest(
                token, settings.admin_api_key
            ):
                await socket.close(code=1008)
                return
            principal = "admin"
        connection = hub.connect(principal)
        logger.info("websocket_connected", extra={"connection_id": connection.id})
        hub.send(
            connection,
            RealtimeEvent(type="ready", data={"connection_id": connection.id, "replay": False}),
        )

        async def reader() -> None:
            while True:
                raw = await receive(settings.websocket_idle_seconds)
                try:
                    command = ClientMessage.model_validate_json(raw)
                except ValidationError:
                    hub.send(
                        connection, RealtimeEvent(type="error", data={"code": "invalid_message"})
                    )
                    continue
                await hub.handle(connection, command)

        async def writer() -> None:
            while True:
                event = await connection.queue.get()
                await asyncio.wait_for(
                    socket.send_text(event.model_dump_json()),
                    settings.websocket_send_timeout_seconds,
                )

        tasks = [
            asyncio.create_task(reader()),
            asyncio.create_task(writer()),
            asyncio.create_task(connection.stopped.wait()),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        close_code = connection.close_code
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        close_code = 1008
    except (ValueError, ValidationError, KeyError):
        close_code = 1008
    except Exception as exc:
        close_code = 1011
        logger.warning("websocket_failed", extra={"error_type": type(exc).__name__})
    finally:
        # TestClient and ASGI servers may cancel the route while children unwind.
        # Shield bounded cleanup so subscriptions cannot leak on disconnect.
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if connection:
                hub.disconnect(connection, close_code)
                logger.info(
                    "websocket_disconnected",
                    extra={"connection_id": connection.id, "close_code": close_code},
                )
            with contextlib.suppress(RuntimeError, OSError, TimeoutError):
                await asyncio.wait_for(socket.close(code=close_code), 2)
