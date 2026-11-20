"""Transport-independent subscription hub with bounded per-client delivery queues."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from app.schemas.realtime import ClientMessage, RealtimeEvent


class ChannelAuthorizer(Protocol):
    """Strategy for resource ownership; implementations must deny unknown channels."""

    async def allows(self, principal: str, channel: str) -> bool: ...


class MarketChannelAuthorizer:
    """Initial policy allows market channels only; chat requires an ownership-aware policy."""

    async def allows(self, principal: str, channel: str) -> bool:
        return bool(re.fullmatch(r"market:[A-Z0-9._-]{1,20}:(quote|1D|1W)", channel))


class EventPublisher(Protocol):
    """Producer boundary; a future Redis bridge can implement cross-process fan-out."""

    async def publish(self, event: RealtimeEvent) -> int: ...


@dataclass(eq=False)
class RealtimeConnection:
    """A connection owns subscriptions and an ordered, bounded outbound queue."""

    principal: str
    queue: asyncio.Queue[RealtimeEvent]
    id: str = field(default_factory=lambda: str(uuid4()))
    channels: set[str] = field(default_factory=set)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int = 1000


class RealtimeService:
    """One event-loop-local hub; delivery is best-effort and slow clients reconnect."""

    def __init__(
        self,
        authorizer: ChannelAuthorizer,
        *,
        queue_size: int = 100,
        max_connections: int = 500,
        max_subscriptions: int = 20,
    ):
        self.authorizer = authorizer
        self.queue_size = queue_size
        self.max_connections = max_connections
        self.max_subscriptions = max_subscriptions
        self.connections: dict[str, RealtimeConnection] = {}
        self.closed = False

    def connect(self, principal: str) -> RealtimeConnection:
        """Register an authenticated client, rejecting capacity/shutdown overflow."""
        if self.closed or len(self.connections) >= self.max_connections:
            raise RuntimeError("realtime_capacity")
        connection = RealtimeConnection(principal, asyncio.Queue(self.queue_size))
        self.connections[connection.id] = connection
        return connection

    def disconnect(self, connection: RealtimeConnection, code: int = 1000) -> None:
        """Idempotent cleanup; wakes the transport without sending from multiple tasks."""
        self.connections.pop(connection.id, None)
        connection.channels.clear()
        connection.close_code = code
        connection.stopped.set()

    def send(self, connection: RealtimeConnection, event: RealtimeEvent) -> bool:
        """Never block all clients behind one slow receiver or silently drop chat tokens."""
        if connection.stopped.is_set():
            return False
        try:
            connection.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.disconnect(connection, 1013)
            return False

    async def handle(self, connection: RealtimeConnection, message: ClientMessage) -> None:
        """Dispatch protocol commands; domain chat/chart processing belongs in separate services."""
        data: dict = {}
        kind = "ack"
        if message.type == "ping":
            kind = "pong"
        elif not message.channel:
            kind, data = "error", {"code": "channel_required"}
        elif message.type == "unsubscribe":
            connection.channels.discard(message.channel)
        elif not await self.authorizer.allows(connection.principal, message.channel):
            kind, data = "error", {"code": "channel_forbidden"}
        elif (
            message.channel not in connection.channels
            and len(connection.channels) >= self.max_subscriptions
        ):
            kind, data = "error", {"code": "subscription_limit"}
        elif not connection.stopped.is_set():
            connection.channels.add(message.channel)
        self.send(
            connection,
            RealtimeEvent(
                type=kind, channel=message.channel, request_id=message.request_id, data=data
            ),
        )

    async def publish(self, event: RealtimeEvent) -> int:
        """Deliver only to subscribed local clients; returns accepted queue count."""
        return sum(
            self.send(client, event)
            for client in list(self.connections.values())
            if event.channel is not None and event.channel in client.channels
        )

    async def close(self) -> None:
        """Stop accepting connections and signal all transports on application shutdown."""
        self.closed = True
        for connection in list(self.connections.values()):
            self.disconnect(connection, 1001)
