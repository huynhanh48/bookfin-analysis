"""Manage chat conversations under the current BookFin owner."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.core.dependencies import AppSettings, DbSession, OwnerId, require_admin_key
from app.core.rag_runtime import get_rag_runtime, get_rag_service
from app.routers.agent import get_agent_service
from app.schemas.chat import (
    ConversationDetail,
    ConversationView,
    RenameConversation,
    SendChatTurn,
    TurnView,
)
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_admin_key)])


def get_chat_service(session: DbSession) -> ChatService:
    return ChatService(session)


ChatDep = Annotated[ChatService, Depends(get_chat_service)]


@router.get("/conversations")
async def list_chats(service: ChatDep, owner: OwnerId) -> list[ConversationView]:
    return await service.list(owner)


@router.post("/conversations", status_code=201)
async def create_chat(service: ChatDep, owner: OwnerId) -> ConversationView:
    return await service.create(owner)


@router.get("/conversations/{chat_id}")
async def read_chat(chat_id: UUID, service: ChatDep, owner: OwnerId) -> ConversationDetail:
    return await service.detail(owner, chat_id)


@router.patch("/conversations/{chat_id}")
async def rename_chat(
    chat_id: UUID,
    payload: RenameConversation,
    service: ChatDep,
    owner: OwnerId,
) -> ConversationView:
    return await service.rename(owner, chat_id, payload.title)


@router.delete("/conversations/{chat_id}", status_code=204)
async def delete_chat(chat_id: UUID, service: ChatDep, owner: OwnerId) -> Response:
    await service.delete(owner, chat_id)
    return Response(status_code=204)


@router.post("/conversations/{chat_id}/turns", status_code=201)
async def send_turn(
    chat_id: UUID,
    payload: SendChatTurn,
    request: Request,
    service: ChatDep,
    owner: OwnerId,
    settings: AppSettings,
) -> TurnView:
    if payload.mode == "research":
        if not settings.agent_enabled:
            raise HTTPException(status_code=503, detail="Agent disabled")
        return await service.send(
            owner, chat_id, payload, settings, agent=get_agent_service(request)
        )
    rag = get_rag_service(get_rag_runtime(request))
    return await service.send(owner, chat_id, payload, settings, rag=rag)
