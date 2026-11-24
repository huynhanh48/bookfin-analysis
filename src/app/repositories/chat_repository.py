"""Owner-scoped PostgreSQL chat persistence."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatTurn, Conversation


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_conversations(self, owner: str) -> Sequence[Conversation]:
        return (
            await self.session.scalars(
                select(Conversation)
                .where(Conversation.owner_id == owner)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            )
        ).all()

    async def get(self, owner: str, chat_id: UUID, *, lock: bool = False) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == chat_id, Conversation.owner_id == owner
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(stmt)

    async def turns(self, chat_id: UUID, limit: int | None = None) -> list[ChatTurn]:
        stmt = select(ChatTurn).where(ChatTurn.conversation_id == chat_id)
        if limit is None:
            return list(await self.session.scalars(stmt.order_by(ChatTurn.position)))
        return list(
            reversed(
                (
                    await self.session.scalars(stmt.order_by(ChatTurn.position.desc()).limit(limit))
                ).all()
            )
        )

    def add(self, item: Conversation | ChatTurn) -> None:
        self.session.add(item)

    async def delete(self, conversation: Conversation) -> None:
        await self.session.delete(conversation)
