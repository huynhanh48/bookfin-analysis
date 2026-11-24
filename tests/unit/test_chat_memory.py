"""Exercise the real LangChain message boundaries with a stubbed provider."""

from unittest.mock import AsyncMock, Mock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.config import Settings
from app.models.chat import ChatTurn
from app.services import chat_service


async def test_follow_up_passes_recent_turns_as_langchain_messages(monkeypatch):
    turns = [ChatTurn(question=f"Câu {idx}", answer=f"Đáp {idx}") for idx in range(10)]
    history = chat_service.history_messages(turns)
    assert len(history) == 16
    assert history[:2] == [HumanMessage(content="Câu 2"), AIMessage(content="Đáp 2")]
    invoke = AsyncMock(return_value=AIMessage(content="RSI của SSI thế nào?"))
    monkeypatch.setattr(chat_service, "ChatOpenRouter", Mock(return_value=Mock(ainvoke=invoke)))
    settings = Settings(openrouter_api_key="test")
    assert (
        await chat_service.contextual_question("Còn RSI?", history, settings)
        == "RSI của SSI thế nào?"
    )
    messages = invoke.await_args.args[0]
    assert isinstance(messages[0], SystemMessage)
    assert messages[1:-1] == history
    assert messages[-1] == HumanMessage(content="Còn RSI?")
