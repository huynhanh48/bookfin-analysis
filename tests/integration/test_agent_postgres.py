"""Real PostgreSQL/SSI checks for the agent's as-of boundary and checkpoint."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import START
from tests.unit.test_agent_graph import FakeSynthesizer

from app.agents.evidence import MarketEvidenceLoader
from app.agents.graph import build_analysis_graph
from app.core.config import get_settings
from app.services.agent_service import checkpoint_dsn

pytestmark = pytest.mark.integration


async def test_ssi_historical_snapshot_excludes_future_candles():
    loader = MarketEvidenceLoader(get_settings())
    latest = await loader.load("SSI", "1D", datetime.now(UTC))
    latest_time = datetime.fromisoformat(latest["market_snapshot"]["data_timestamp"])
    earlier_at = latest_time - timedelta(days=30)
    earlier = await loader.load("SSI", "1D", earlier_at)
    earlier_time = datetime.fromisoformat(earlier["market_snapshot"]["data_timestamp"])
    assert earlier_time < latest_time
    assert earlier_time <= earlier_at
    assert earlier["market_snapshot"]["candle_status"] == "CONFIRMED"
    assert all(
        datetime.fromisoformat(event["confirmed_at"]) <= earlier_time
        for event in earlier["divergence_events"]
    )


async def test_checkpoint_survives_a_new_postgres_connection():
    settings = get_settings()
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initial = {
        "analysis_id": thread_id,
        "user_id": "local",
        "symbol": "SSI",
        "timeframe": "1D",
        "analyzed_at": "2026-11-19T00:00:00+00:00",
        "question": "Phân tích SSI",
    }
    from tests.unit.test_agent_graph import FakeLoader

    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn(settings.database_url)) as saver:
        graph = build_analysis_graph(FakeLoader(), FakeSynthesizer(), saver)
        await graph.aupdate_state(config, initial, as_node=START)
        result = await graph.ainvoke(None, config)
        assert result["final_report"]["final_conclusion"]
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn(settings.database_url)) as saver:
        graph = build_analysis_graph(FakeLoader(), FakeSynthesizer(), saver)
        state = await graph.aget_state(config)
        assert state.values["analysis_id"] == thread_id
        assert state.next == ()


async def test_research_builds_multi_symbol_report_from_real_candles():
    from app.agents.graph import UnconfiguredReportSynthesizer
    from app.agents.research import ResearchContext, resolve_request
    from app.schemas.agent import AnalyzeRequest

    settings = get_settings()
    request = AnalyzeRequest(question="So sánh SSI và HPG D1 ngày 19/11/2026")
    symbols, timeframe, cutoff = await resolve_request(request, settings.market_tz)
    assert cutoff is not None
    graph = build_analysis_graph(
        MarketEvidenceLoader(settings),
        UnconfiguredReportSynthesizer(),
        context=ResearchContext(None, settings),
    )
    result = await graph.ainvoke(
        {
            "symbol": symbols[0],
            "symbols": symbols,
            "timeframe": timeframe,
            "analyzed_at": cutoff.isoformat(),
            "question": request.question,
        }
    )
    assert set(result["technical_summary"]) == {"SSI", "HPG"}
    for item in result["technical_summary"].values():
        assert "rsi" in item["fingerprint"]
        assert {"adx", "ichimoku"} <= {finding["indicator"] for finding in item["findings"]}
    assert "## SSI" in result["markdown"] and "## HPG" in result["markdown"]
