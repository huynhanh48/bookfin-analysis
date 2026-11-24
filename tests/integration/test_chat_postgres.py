"""Verify the chat HTTP contract with PostgreSQL and deterministic answer engines."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import func, select

from app.adapters.rag_adapter import RagAnswer
from app.core.config import get_settings
from app.core.database import session_scope
from app.core.dependencies import get_owner_id
from app.main import app
from app.models.chat import ChatTurn
from app.routers import chat
from app.services import chat_service
from app.services.agent_service import AgentChatAnswer

pytestmark = pytest.mark.integration


async def test_transcript_memory_rename_owner_and_delete(client, monkeypatch):
    agent = AsyncMock()
    agent.answer_chat.side_effect = [
        AgentChatAnswer("SSI: kết quả thứ nhất."),
        AgentChatAnswer("SSI: kết quả tiếp theo."),
    ]
    monkeypatch.setattr(chat, "get_agent_service", lambda _: agent)
    rewrite = AsyncMock(
        side_effect=["Phân tích SSI", "RSI của SSI thế nào?", "Tài liệu nào nói về RSI của SSI?"]
    )
    monkeypatch.setattr(chat_service, "contextual_question", rewrite)

    rag = AsyncMock()
    rag.retrieve_financial_knowledge.return_value = RagAnswer("Tài liệu SSI", [{"source": "notes"}])
    monkeypatch.setattr(chat, "get_rag_runtime", lambda _: None)
    monkeypatch.setattr(chat, "get_rag_service", lambda _: rag)
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    owner = "test-chat-first"
    app.dependency_overrides[get_owner_id] = lambda: owner
    url = "/api/v1/chat/conversations"
    created = await client.post(url)
    assert created.status_code == 201
    chat_id = created.json()["id"]
    path = f"{url}/{chat_id}"
    try:
        for question in ("Phân tích SSI", "Còn RSI thì sao?"):
            response = await client.post(
                f"{path}/turns", json={"mode": "research", "question": question}
            )
            assert response.status_code == 201, response.text
        assert agent.answer_chat.await_args.args[2] == [
            HumanMessage(content="Phân tích SSI"),
            AIMessage(content="SSI: kết quả thứ nhất."),
        ]
        assert agent.answer_chat.await_args.kwargs["follow_up"] == "RSI của SSI thế nào?"
        knowledge = await client.post(
            f"{path}/turns", json={"mode": "knowledge", "question": "Kho có tài liệu nào?"}
        )
        assert knowledge.status_code == 201
        assert knowledge.json()["sources"] == [{"source": "notes"}]
        rag.retrieve_financial_knowledge.assert_awaited_once_with(
            "Tài liệu nào nói về RSI của SSI?"
        )
        detail = (await client.get(path)).json()
        assert [turn["position"] for turn in detail["turns"]] == [1, 2, 3]
        renamed = await client.patch(path, json={"title": "  Phân tích SSI  "})
        assert renamed.json()["title"] == "Phân tích SSI"

        owner = "test-chat-other"
        assert not (await client.get(url)).json()
        assert (await client.get(path)).status_code == 404
        assert (await client.delete(path)).status_code == 404
    finally:
        owner = "test-chat-first"
        deleted = await client.delete(path)
        app.dependency_overrides.pop(get_owner_id, None)
    assert deleted.status_code == 204
    async with session_scope() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ChatTurn)
                .where(ChatTurn.conversation_id == UUID(chat_id))
            )
            == 0
        )
