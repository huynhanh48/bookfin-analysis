"""Job registry, params validation và thứ tự lịch chạy."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.workers.jobs.indicators import DivergenceScanParams, RecomputeParams
from app.workers.jobs.ingestion import IngestCloseParams
from app.workers.registry import (
    DuplicateJobError,
    EmptyParams,
    JobContext,
    JobRegistry,
    JobResult,
    JobSpec,
    SymbolSelection,
    UnknownJobError,
    discover_jobs,
)
from app.workers.schedule import DailySchedule, IntervalSchedule, describe_schedule


async def noop(ctx: JobContext[EmptyParams]) -> JobResult:
    """Handler rỗng dùng cho test registry."""
    return JobResult()


def test_default_registry_contains_required_jobs():
    registry = discover_jobs()
    assert {
        "sync-symbols",
        "ingest-close",
        "recompute-indicators",
        "divergence-scan",
        "rag-ingestion",
    } <= set(registry.names())


def test_default_schedules_preserve_existing_times():
    registry = discover_jobs()
    expected = {
        "sync-symbols": (8, 0),
        "ingest-close": (15, 5),
        "recompute-indicators": (15, 20),
        "divergence-scan": (15, 30),
    }
    for name, (hour, minute) in expected.items():
        schedule = registry.get(name).schedule
        assert isinstance(schedule, DailySchedule)
        assert (schedule.hour, schedule.minute) == (hour, minute)
    assert registry.get("rag-ingestion").schedule is None


def test_registry_rejects_duplicate_and_unknown_names():
    registry = JobRegistry()
    spec = JobSpec(name="demo", handler=noop, params_model=EmptyParams)
    registry.register(spec)
    with pytest.raises(DuplicateJobError):
        registry.register(spec)
    registry.register(spec, replace=True)
    with pytest.raises(UnknownJobError):
        registry.get("missing")


def test_scheduled_jobs_are_sorted_by_time_not_import_order():
    registry = JobRegistry()
    registry.register(
        JobSpec(
            name="late",
            handler=noop,
            params_model=EmptyParams,
            schedule=DailySchedule(15, 20),
        )
    )
    registry.register(
        JobSpec(
            name="early",
            handler=noop,
            params_model=EmptyParams,
            schedule=DailySchedule(15, 5),
        )
    )
    registry.register(
        JobSpec(
            name="periodic",
            handler=noop,
            params_model=EmptyParams,
            schedule=IntervalSchedule(60),
        )
    )

    assert [spec.name for spec in registry.scheduled()] == ["early", "late", "periodic"]


def test_symbol_selection_accepts_comma_string_and_uppercases():
    params = IngestCloseParams(symbols=" fpt, hpg ,,")
    assert params.symbols == ["FPT", "HPG"]


def test_job_params_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        IngestCloseParams(symbols="FPT", typo=1)


def test_recompute_end_requires_timezone():
    with pytest.raises(ValidationError):
        RecomputeParams(end=datetime(2026, 10, 31, 15, 1))
    aware = RecomputeParams(end=datetime.fromisoformat("2026-10-31T15:01:00+07:00"))
    assert aware.end is not None


def test_divergence_defaults_match_previous_worker():
    params = DivergenceScanParams()
    assert (params.days, params.limit, params.timeframe) == (1, 600, "1D")


def test_describe_schedule():
    assert describe_schedule(DailySchedule(15, 5)) == "weekdays 15:05"
    assert describe_schedule(DailySchedule(9, 0, weekdays_only=False)) == "daily 09:00"
    assert describe_schedule(IntervalSchedule(60)) == "every 60s"
    assert describe_schedule(None) == "manual"


def test_symbol_schedule_models_are_immutable_and_validated():
    with pytest.raises(ValueError):
        DailySchedule(24, 0)
    with pytest.raises(ValueError):
        IntervalSchedule(1)
    assert SymbolSelection(symbols=["fpt"]).symbols == ["FPT"]


def test_settings_worker_defaults_are_bounded():
    settings = Settings(_env_file=None)
    assert settings.worker_shutdown_grace_seconds >= 1
    assert settings.worker_idempotency_ttl_seconds >= 60
    assert settings.worker_session_close_time == "15:00"
