"""Basic conversation must not depend on retrieval or paid providers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.models.chat import Conversation
from app.schemas.chat import SendChatTurn
from app.services import chat_service
from app.services.chat_smalltalk import (
    CAPABILITY_ANSWER,
    FAREWELL_ANSWER,
    GREETING_ANSWER,
    THANKS_ANSWER,
    WELLBEING_ANSWER,
    smalltalk_answer,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("xin chào", GREETING_ANSWER),
        ("Chào bạn!", GREETING_ANSWER),
        ("XIN CHAO", GREETING_ANSWER),
        ("hello", GREETING_ANSWER),
        ("hi", GREETING_ANSWER),
        ("cảm ơn bạn nhiều", THANKS_ANSWER),
        ("Thanks!", THANKS_ANSWER),
        ("tạm biệt nhé", FAREWELL_ANSWER),
        ("bạn khỏe không", WELLBEING_ANSWER),
        ("bạn làm được gì?", CAPABILITY_ANSWER),
        ("hướng dẫn sử dụng", CAPABILITY_ANSWER),
    ],
)
def test_smalltalk_phrases(question, expected):
    assert smalltalk_answer(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "SSI có xu hướng gì?",
        "Chào bạn, phân tích SSI giúp mình",
        "hướng dẫn phân tích SSI",
        "cảm ơn, RSI của SSI bao nhiêu?",
        "",
        "   ",
    ],
)
def test_real_questions_are_not_intercepted(question):
    assert smalltalk_answer(question) is None


async def test_send_greeting_skips_context_rewrite_and_rag(monkeypatch):
    """A greeting is answered locally and never spends a retrieval call."""
    session = MagicMock()
    session.commit = AsyncMock()
    service = chat_service.ChatService(session)
    chat = Conversation(
        owner_id="owner",
        title=chat_service.DEFAULT_TITLE,
        revision=0,
        updated_at=datetime.now(UTC),
    )
    service.require = AsyncMock(return_value=chat)
    service.repo.turns = AsyncMock(return_value=[])
    service.repo.add = Mock(side_effect=lambda turn: setattr(turn, "created_at", datetime.now(UTC)))
    rewrite = AsyncMock()
    monkeypatch.setattr(chat_service, "contextual_question", rewrite)
    rag = MagicMock()
    rag.retrieve_financial_knowledge = AsyncMock()

    view = await service.send(
        "owner",
        uuid4(),
        SendChatTurn(question="xin chào", mode="knowledge"),
        Settings(_env_file=None),
        rag=rag,
    )

    rewrite.assert_not_awaited()
    rag.retrieve_financial_knowledge.assert_not_awaited()
    assert view.answer == GREETING_ANSWER
    assert view.sources == []
