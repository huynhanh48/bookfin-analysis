"""Heartbeat file, freshness và healthcheck theo role."""

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.workers.heartbeat import (
    Heartbeat,
    check_role_health,
    heartbeat_is_fresh,
    heartbeat_path,
    read_heartbeat,
)


def make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        scheduler_heartbeat_file=str(tmp_path / "scheduler.heartbeat"),
        worker_heartbeat_dir=str(tmp_path / "workers"),
    )


def test_heartbeat_roundtrip_and_freshness(tmp_path):
    settings = make_settings(tmp_path)
    path = heartbeat_path(settings, "realtime")
    heartbeat = Heartbeat(path, "realtime")
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

    heartbeat.write(status="ok", detail={"ticks": 3}, now=now)

    payload = read_heartbeat(path)
    assert payload is not None
    assert payload["role"] == "realtime"
    assert payload["status"] == "ok"
    assert payload["detail"] == {"ticks": 3}
    assert heartbeat_is_fresh(path, max_age_seconds=30, now=now + timedelta(seconds=10))
    assert not heartbeat_is_fresh(path, max_age_seconds=30, now=now + timedelta(seconds=31))


def test_scheduler_uses_legacy_heartbeat_path(tmp_path):
    settings = make_settings(tmp_path)
    assert heartbeat_path(settings, "scheduler") == tmp_path / "scheduler.heartbeat"
    assert heartbeat_path(settings, "realtime") == tmp_path / "workers" / "realtime.heartbeat"


def test_check_role_health_reports_missing_and_stale(tmp_path):
    settings = make_settings(tmp_path)
    ok, payload = check_role_health(settings, "scheduler")
    assert ok is False
    assert payload is None

    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    Heartbeat(heartbeat_path(settings, "scheduler"), "scheduler").write(now=now)
    ok, payload = check_role_health(
        settings, "scheduler", max_age_seconds=10, now=now + timedelta(seconds=20)
    )
    assert ok is False
    assert payload is not None

    ok, _ = check_role_health(
        settings, "scheduler", max_age_seconds=30, now=now + timedelta(seconds=20)
    )
    assert ok is True
