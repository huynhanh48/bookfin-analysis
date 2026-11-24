"""Research rules and graph fallbacks."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from tests.unit.test_agent_graph import FakeLoader

from app.agents.graph import UnconfiguredReportSynthesizer, build_analysis_graph
from app.agents.research import evaluate_signals, resolve_request
from app.core.exceptions import InvalidRequestError
from app.schemas.agent import AnalyzeRequest


async def test_resolve_multiple_symbols_and_date(monkeypatch):
    from contextlib import asynccontextmanager

    from app.agents import research

    @asynccontextmanager
    async def session():
        yield object()

    class Repository:
        def __init__(self, session):
            pass

        async def get_symbol(self, symbol):
            return symbol in {"SSI", "HPG"}

    monkeypatch.setattr(research, "session_scope", session)
    monkeypatch.setattr(research, "MarketRepository", Repository)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    symbols, timeframe, cutoff = await resolve_request(
        AnalyzeRequest(question="So sánh SSI và HPG W1 ngày 2024-04-30"), tz
    )
    assert symbols == ["SSI", "HPG"]
    assert timeframe == "1W"
    assert cutoff.isoformat() == "2024-04-30T23:59:59.999999+07:00"
    with pytest.raises(InvalidRequestError):
        await resolve_request(AnalyzeRequest(symbol="INVALID"), tz)


def test_rsi_alert_and_dmi_direction_are_independent():
    evidence = {
        "market_snapshot": {"data_timestamp": datetime.now(UTC).isoformat()},
        "indicator_values": {
            key: {"indicator": key, "values": {key: value}}
            for key, value in {"rsi": 75, "adx": 45, "plus_di": 10, "minus_di": 35}.items()
        },
    }
    rows = {item["indicator"]: item for item in evaluate_signals(evidence, "1D")}
    assert rows["rsi"]["direction"] == "neutral"
    assert rows["rsi"]["alerts"] == ["overbought"]
    assert rows["adx"]["direction"] == "bearish"


async def test_optional_provider_errors_preserve_two_symbols_and_markdown():
    class Context:
        async def cases(self, state):
            raise TimeoutError

        async def news(self, state):
            return []

    graph = build_analysis_graph(
        FakeLoader(), UnconfiguredReportSynthesizer(), InMemorySaver(), Context()
    )
    result = await graph.ainvoke(
        {
            "symbol": "SSI",
            "symbols": ["SSI", "HPG"],
            "timeframe": "1D",
            "analyzed_at": "2026-11-19T00:00:00+00:00",
            "question": "SSI và HPG",
        },
        {"configurable": {"thread_id": "research-fallback"}},
    )
    assert set(result["technical_summary"]) == {"SSI", "HPG"}
    assert "cases_unavailable" in result["warnings"]
    assert "model_unavailable" in result["warnings"]
    assert "SSI" in result["markdown"] and "HPG" in result["markdown"]
    assert "HPG · RSI(14)" in result["markdown"]
    assert "CLOSED_ONLY" not in result["markdown"]
    assert "break_bias" not in result["markdown"]
    assert result["report_card"]["symbols"] == ["SSI", "HPG"]
    assert result["report_card"]["indicators"]
