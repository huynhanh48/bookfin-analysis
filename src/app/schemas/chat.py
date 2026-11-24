"""Conversation and chat turn contracts."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


class RenameConversation(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SendChatTurn(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    mode: Literal["knowledge", "research"]


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    updated_at: datetime


class TurnView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    question: str
    answer: str
    sources: list[dict[str, Any]]
    report: dict[str, Any] | None = None
    created_at: datetime


class ConversationDetail(ConversationView):
    turns: list[TurnView]
