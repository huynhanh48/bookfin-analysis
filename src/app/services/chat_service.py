"""Coordinate PostgreSQL transcripts and the existing answer engines."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, UpstreamError, UpstreamTimeoutError
from app.models.chat import ChatTurn, Conversation
from app.repositories.chat_repository import ChatRepository
from app.schemas.agent import AnalyzeRequest
from app.schemas.chat import ConversationDetail, ConversationView, SendChatTurn, TurnView
from app.services.agent_service import AgentService
from app.services.chat_smalltalk import smalltalk_answer
from app.services.rag_service import RagService

HISTORY_TURNS = 8
DEFAULT_TITLE = "Hội thoại mới"


def history_messages(turns: Sequence[ChatTurn]) -> list[BaseMessage]:
    """Build role-preserving context from the most recent completed turns."""
    messages: list[BaseMessage] = []
    for turn in turns[-HISTORY_TURNS:]:
        messages.extend(
            (HumanMessage(content=turn.question), AIMessage(content=turn.answer[:2500]))
        )
    return messages


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ChatRepository(session)

    async def require(self, owner: str, chat_id: UUID, *, lock: bool = False) -> Conversation:
        chat = await self.repo.get(owner, chat_id, lock=lock)
        if chat is None:
            raise NotFoundError("Không tìm thấy hội thoại.")
        return chat

    async def list(self, owner: str) -> list[ConversationView]:
        return [ConversationView.model_validate(row) for row in await self.repo.list_conversations(owner)]

    async def create(self, owner: str) -> ConversationView:
        chat = Conversation(owner_id=owner, title=DEFAULT_TITLE)
        self.repo.add(chat)
        await self.session.commit()
        return ConversationView.model_validate(chat)

    async def detail(self, owner: str, chat_id: UUID) -> ConversationDetail:
        chat = await self.require(owner, chat_id)
        return ConversationDetail(
            id=chat.id,
            title=chat.title,
            updated_at=chat.updated_at,
            turns=[TurnView.model_validate(row) for row in await self.repo.turns(chat_id)],
        )

    async def rename(self, owner: str, chat_id: UUID, title: str) -> ConversationView:
        chat = await self.require(owner, chat_id, lock=True)
        chat.title = title
        await self.session.commit()
        return ConversationView.model_validate(chat)

    async def delete(self, owner: str, chat_id: UUID) -> None:
        await self.repo.delete(await self.require(owner, chat_id, lock=True))
        await self.session.commit()

    async def send(
        self,
        owner: str,
        chat_id: UUID,
        payload: SendChatTurn,
        settings: Settings,
        *,
        agent: AgentService | None = None,
        rag: RagService | None = None,
    ) -> TurnView:
        """Keep provider I/O outside transactions; reject stale concurrent replies."""
        chat = await self.require(owner, chat_id, lock=True)
        revision = chat.revision
        history = history_messages(await self.repo.turns(chat_id, HISTORY_TURNS))
        await self.session.commit()
        try:
            async with asyncio.timeout(settings.agent_timeout_seconds + 30):
                sources = []
                report = None
                answer = smalltalk_answer(payload.question)
                if answer is None:
                    question = await contextual_question(payload.question, history, settings)
                    if agent is not None:
                        result = await agent.answer_chat(
                            AnalyzeRequest(question=payload.question),
                            owner,
                            history,
                            follow_up=question if question != payload.question else "",
                        )
                        answer, report = result.answer, result.report
                    elif rag is not None:
                        result = await rag.retrieve_financial_knowledge(question)
                        answer, sources = result.answer, result.sources
                    else:
                        raise UpstreamError("Chế độ chat chưa sẵn sàng.")
        except TimeoutError as exc:
            raise UpstreamTimeoutError("Quá thời gian chờ phản hồi.") from exc

        chat = await self.require(owner, chat_id, lock=True)
        if chat.revision != revision:
            raise ConflictError("Hội thoại vừa có tin nhắn mới. Vui lòng gửi lại.")
        if revision == 0 and chat.title == DEFAULT_TITLE:
            chat.title = " ".join(payload.question.split())[:120]
        chat.revision += 1
        chat.updated_at = datetime.now(UTC)
        turn = ChatTurn(
            conversation_id=chat_id,
            position=chat.revision,
            question=payload.question,
            answer=answer,
            sources=sources,
            report=report,
        )
        self.repo.add(turn)
        await self.session.commit()
        return TurnView.model_validate(turn)


async def contextual_question(question: str, history: list[BaseMessage], settings: Settings) -> str:
    """Resolve follow-up references before selecting market evidence or querying RAG."""
    if not history or not settings.openrouter_api_key:
        return question
    model = ChatOpenRouter(
        api_key=SecretStr(settings.openrouter_api_key),
        base_url=settings.openrouter_base_url,
        model=settings.agent_model,
        temperature=0,
        timeout=12000,
        max_retries=0,
    )
    async with asyncio.timeout(12):
        reply = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Viết lại câu hỏi cuối thành một câu hỏi độc lập bằng tiếng Việt. "
                        "Chỉ dùng lịch sử để giải nghĩa tham chiếu, ví dụ mã cổ phiếu được nhắc trước đó. "
                        "Giữ nguyên mã, ngày và khung thời gian được nêu rõ trong câu hỏi cuối. "
                        "Không tự thêm hoặc kế thừa ngày và không trả lời câu hỏi. "
                        "Nếu không cần ngữ cảnh thì lặp lại nguyên văn câu hỏi cuối."
                    )
                ),
                *history,
                HumanMessage(content=question),
            ]
        )
    text = reply.text.strip()
    return text if 3 <= len(text) <= 2000 else question
