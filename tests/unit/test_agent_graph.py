"""Phase 1 graph tests use deterministic evidence and no paid provider calls."""

import asyncio
from datetime import UTC, datetime

import langchain_openrouter
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.agents.graph import OpenRouterReportSynthesizer, build_analysis_graph
from app.schemas.agent import AnalyzeRequest, StockReport
from app.services.agent_service import checkpoint_dsn


class FakeLoader:
    def __init__(self):
        self.calls = 0

    async def load(self, symbol, timeframe, analyzed_at, include_live=False):
        self.calls += 1
        assert analyzed_at == datetime(2026, 11, 19, tzinfo=UTC)
        return {
            "market_snapshot": {
                "symbol": symbol,
                "timeframe": timeframe,
                "data_timestamp": "2026-11-18T00:00:00+00:00",
                "candle_status": "CONFIRMED",
                "volume": 2_000_000,
                "close": 20_000,
                "previous_close": 21_000,
                "previous_volume": 1_000_000,
            },
            "indicator_values": {
                "rsi(period=14)": {"indicator": "rsi", "values": {"rsi": 42}},
                "macd": {
                    "indicator": "macd",
                    "values": {"histogram": -10},
                    "signal": "below_signal",
                },
                "rvol": {
                    "indicator": "rvol",
                    "values": {"rvol": 1.6},
                    "signal": "above_average",
                },
            },
            "structure": {"confirmed_trend": "DOWNTREND", "swings": [], "events": []},
            "divergence_events": [],
        }


class FakeSynthesizer:
    async def synthesize(self, evidence):
        assert evidence["market"]["candle_status"] == "CONFIRMED"
        assert evidence["momentum"]["evidence"]["rsi"] == 42
        return StockReport(
            market_overview="Giá đóng cửa 20.000",
            volume_analysis="Khối lượng cao hơn trung bình",
            momentum_analysis="MACD dưới đường tín hiệu",
            trend_market_structure="Xu hướng giảm",
            divergence_analysis="Chưa có phân kỳ đã xác nhận",
            final_conclusion="Bằng chứng trái chiều; cần theo dõi thêm.",
        )


@pytest.mark.asyncio
async def test_graph_runs_parallel_technical_nodes_and_checkpoints():
    loader = FakeLoader()
    graph = build_analysis_graph(loader, FakeSynthesizer(), InMemorySaver())
    config = {"configurable": {"thread_id": "unit-agent-1"}}
    result = await graph.ainvoke(
        {
            "analysis_id": "unit-agent-1",
            "user_id": "local",
            "symbol": "SSI",
            "timeframe": "1D",
            "analyzed_at": "2026-11-19T00:00:00+00:00",
            "question": "Phân tích SSI",
        },
        config,
    )
    assert loader.calls == 1
    assert result["volume_analysis"]["signal"] == "above_average"
    assert result["volume_analysis"]["direction"] == "bearish"
    assert result["momentum_analysis"]["direction"] == "bearish"
    assert result["trend_analysis"]["signal"] == "DOWNTREND"
    assert result["divergence_analysis"]["signal"] == "none_confirmed_in_window"
    assert result["final_report"]["references"][0].startswith("fcdata:SSI:")
    assert {
        "market_ms",
        "volume_ms",
        "momentum_ms",
        "trend_ms",
        "divergence_ms",
        "conclusion_ms",
    } <= set(result["execution_metadata"])
    history = [step async for step in graph.aget_state_history(config)]
    assert any({"volume", "momentum", "trend", "divergence"} <= set(step.next) for step in history)


def test_request_rejects_naive_historical_time():
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="SSI", analyzed_at=datetime(2026, 11, 19))
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol="SSI", analyzed_at=datetime(2999, 1, 1, tzinfo=UTC))


def test_checkpointer_uses_psycopg_dsn():
    result = checkpoint_dsn("postgresql+asyncpg://stock:secret@localhost:5432/bookfin")
    assert result == "postgresql://stock:secret@localhost:5432/bookfin"


def test_openrouter_timeout_is_passed_in_milliseconds(monkeypatch):
    options = {}

    class FakeChatOpenRouter:
        def __init__(self, **kwargs):
            options.update(kwargs)

        def with_structured_output(self, schema, *, method):
            assert schema is StockReport
            assert method == "function_calling"
            return object()

    monkeypatch.setattr(langchain_openrouter, "ChatOpenRouter", FakeChatOpenRouter)
    OpenRouterReportSynthesizer(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
        timeout=12.5,
    )
    assert options["timeout"] == 12_500


@pytest.mark.asyncio
async def test_failed_synthesis_resumes_from_checkpoint_without_reloading_market():
    class FlakySynthesizer(FakeSynthesizer):
        calls = 0

        async def synthesize(self, evidence):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("provider timeout")
            return await super().synthesize(evidence)

    loader = FakeLoader()
    synthesizer = FlakySynthesizer()
    graph = build_analysis_graph(loader, synthesizer, InMemorySaver())
    config = {"configurable": {"thread_id": "unit-agent-resume"}}
    initial = {
        "analysis_id": "unit-agent-resume",
        "user_id": "local",
        "symbol": "SSI",
        "timeframe": "1D",
        "analyzed_at": "2026-11-19T00:00:00+00:00",
        "question": "Phân tích SSI",
    }
    with pytest.raises(TimeoutError):
        await graph.ainvoke(initial, config)
    assert loader.calls == 1
    resumed = await graph.ainvoke(None, config)
    assert resumed["final_report"]["final_conclusion"]
    assert loader.calls == 1
    assert synthesizer.calls == 2


@pytest.mark.asyncio
async def test_synthesis_has_total_wall_clock_timeout():
    class HangingChain:
        async def ainvoke(self, messages):
            await asyncio.sleep(1)

    synthesizer = OpenRouterReportSynthesizer.__new__(OpenRouterReportSynthesizer)
    synthesizer._chain = HangingChain()
    synthesizer._timeout = 0.01
    with pytest.raises(TimeoutError):
        await synthesizer.synthesize({"market": {"symbol": "SSI"}})
