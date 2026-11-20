"""Job handler: cô lập lỗi từng mã và không đánh dấu complete khi còn lỗi."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.core.config import Settings
from app.core.exceptions import InsufficientDataError
from app.services.ingestion_service import BackfillStats
from app.workers.jobs.indicators import RecomputeParams, recompute_symbols
from app.workers.jobs.ingestion import IngestCloseParams, ingest_close
from app.workers.registry import JobContext


def make_settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


def context(name, params):
    return JobContext(
        job=name,
        run_id="test-run",
        trigger="manual",
        params=params,
        settings=make_settings(),
    )


async def test_ingest_close_counts_partial_failure(monkeypatch):
    import app.workers.jobs.ingestion as module

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        async def backfill_symbol(self, symbol, *, timeframe, start, end):
            del timeframe, start, end
            if symbol == "BAD":
                raise RuntimeError("upstream")
            return BackfillStats(symbol=symbol, inserted=2, rejected=0, pages=1)

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    monkeypatch.setattr(module, "FcDataClient", lambda settings: FakeClient())
    monkeypatch.setattr(module, "IngestionService", FakeService)
    monkeypatch.setattr(module, "session_scope", fake_session_scope)

    result = await ingest_close(context("ingest-close", IngestCloseParams(symbols=["FPT", "BAD"])))

    assert result.summary["requested"] == 2
    assert result.summary["inserted"] == 2
    assert result.summary["failed"] == 1
    assert result.complete is False


async def test_recompute_counts_skipped_and_failed_symbols(monkeypatch):
    import app.workers.jobs.indicators as module

    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        async def recompute(self, symbol, timeframe, *, end=None):
            del timeframe, end
            if symbol == "EMPTY":
                raise InsufficientDataError("chưa đủ nến")
            if symbol == "BAD":
                raise ValueError("database")
            return SimpleNamespace(results=[object(), object()], candles_used=120)

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    monkeypatch.setattr(module, "IndicatorService", FakeService)
    monkeypatch.setattr(module, "session_scope", fake_session_scope)

    result = await recompute_symbols(
        make_settings(), RecomputeParams(symbols=["FPT", "EMPTY", "BAD"])
    )

    assert result.summary["requested"] == 3
    assert result.summary["recomputed"] == 1
    assert result.summary["skipped"] == 1
    assert result.summary["failed"] == 1
    assert result.complete is False
