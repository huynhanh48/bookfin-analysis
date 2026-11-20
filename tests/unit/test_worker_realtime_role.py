"""Realtime role: trigger đóng phiên, snapshot provisional và lifecycle."""

import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.fcdata.stream import QuoteTick
from app.workers.engine import JobOutcome, JobStatus
from app.workers.heartbeat import Heartbeat, read_heartbeat
from app.workers.roles.realtime import (
    RealtimeRole,
    is_session_close_due,
    parse_close_time,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class Clock:
    """Đồng hồ điều khiển được cho test."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeEngine:
    """Engine ghi lại job được realtime kích hoạt."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def execute(self, name, params=None, *, trigger="manual", force=False):
        del force
        self.calls.append((name, trigger, params))
        return JobOutcome(job=name, run_id="fake", status=JobStatus.SUCCEEDED)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        worker_heartbeat_seconds=1,
        worker_shutdown_grace_seconds=1,
        worker_session_check_seconds=5,
        worker_session_close_time="15:00",
    )


def make_role(tmp_path, clock: Clock, engine: FakeEngine) -> RealtimeRole:
    return RealtimeRole(
        make_settings(),
        engine,
        symbols=["FPT", "HPG"],
        indices=["VNINDEX"],
        heartbeat=Heartbeat(tmp_path / "realtime.heartbeat", "realtime"),
        redis=None,
        clock=clock,
    )


def test_parse_close_time():
    assert parse_close_time("15:00") == time(15, 0)
    with pytest.raises(ValueError):
        parse_close_time("25:00")
    with pytest.raises(ValueError):
        parse_close_time("bad")


def test_session_close_due_handles_time_weekend_holiday_and_once():
    close_at = time(15, 0)
    friday = datetime(2026, 11, 20, 15, 1, tzinfo=TZ)
    assert is_session_close_due(friday, close_at, last_trigger=None, holidays=frozenset())
    assert not is_session_close_due(
        friday, close_at, last_trigger=date(2026, 11, 20), holidays=frozenset()
    )
    assert not is_session_close_due(
        datetime(2026, 11, 20, 14, 59, tzinfo=TZ),
        close_at,
        last_trigger=None,
        holidays=frozenset(),
    )
    assert not is_session_close_due(
        datetime(2026, 11, 21, 15, 1, tzinfo=TZ),
        close_at,
        last_trigger=None,
        holidays=frozenset(),
    )
    assert not is_session_close_due(
        friday, close_at, last_trigger=None, holidays=frozenset({date(2026, 11, 20)})
    )


async def test_session_close_triggers_ingest_then_recompute_once(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 15, 5, tzinfo=TZ))
    engine = FakeEngine()
    role = make_role(tmp_path, clock, engine)

    await role.trigger_session_close_if_due()
    await role.trigger_session_close_if_due()

    assert [(name, trigger) for name, trigger, _ in engine.calls] == [
        ("ingest-close", "realtime"),
        ("recompute-indicators", "realtime"),
    ]
    ingest_params = engine.calls[0][2]
    assert ingest_params["symbols"] == ["FPT", "HPG"]
    assert engine.calls[1][2]["symbols"] == ["FPT", "HPG"]


async def test_on_tick_updates_forming_snapshot_without_database(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 10, 0, tzinfo=TZ))
    engine = FakeEngine()
    role = make_role(tmp_path, clock, engine)

    await role.on_tick(QuoteTick("FPT", "X-TRADE", price=10.5, volume=100))

    assert role._health_detail()["ticks"] == 1
    assert len(role._aggregator.forming_snapshot()) == 1
    assert engine.calls == []


async def test_role_stops_stream_and_writes_heartbeat(tmp_path):
    clock = Clock(datetime(2026, 11, 20, 10, 0, tzinfo=TZ))
    engine = FakeEngine()
    stopped = asyncio.Event()

    class FakeStream:
        async def run(self, stop_event=None):
            await stop_event.wait()
            stopped.set()

    def factory(settings, on_tick, symbols, indices):
        del settings, on_tick, symbols, indices
        return FakeStream()

    role = RealtimeRole(
        make_settings(),
        engine,
        symbols=["FPT"],
        indices=[],
        heartbeat=Heartbeat(tmp_path / "realtime.heartbeat", "realtime"),
        redis=None,
        clock=clock,
        stream_factory=factory,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(role.run(stop))
    await asyncio.sleep(0)
    stop.set()

    await asyncio.wait_for(task, timeout=3)

    assert stopped.is_set()
    heartbeat = read_heartbeat(tmp_path / "realtime.heartbeat")
    assert heartbeat is not None
    assert heartbeat["role"] == "realtime"
