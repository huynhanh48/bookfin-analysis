"""Versioned realtime wire contract shared by chart and future chat channels."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ClientMessage(BaseModel):
    """Commands are bounded; clients cannot publish arbitrary server events."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["ping", "subscribe", "unsubscribe"]
    request_id: str | None = Field(default=None, max_length=100)
    channel: str | None = Field(default=None, max_length=150, pattern=r"^[a-zA-Z0-9:._-]+$")


class RealtimeEvent(BaseModel):
    """Event ID supports client deduplication; timestamps do not imply replay support."""

    version: Literal[1] = 1
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str = Field(min_length=1, max_length=100)
    channel: str | None = None
    request_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)
