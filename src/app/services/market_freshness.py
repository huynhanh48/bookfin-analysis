"""Trading-session freshness helpers shared by the agent and report layers."""

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def previous_weekday(day: date) -> date:
    """Return the latest weekday strictly before ``day``."""
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def expected_session_date(analyzed_at: datetime, tz: ZoneInfo, market_close: time) -> date:
    """Expected last closed daily session at analysis time (weekday approximation).

    Holidays are intentionally not guessed. If FCData has no newer candle after a
    targeted backfill, the caller records ``source_no_newer`` instead of claiming
    the data is wrong.
    """
    local = analyzed_at.astimezone(tz)
    day = local.date() if local.time() >= market_close else previous_weekday(local.date())
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def weekday_sessions_between(latest: date, expected: date) -> int:
    """Count weekdays after ``latest`` through ``expected`` (inclusive)."""
    if latest >= expected:
        return 0
    count = 0
    current = latest + timedelta(days=1)
    while current <= expected:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def freshness_payload(
    *,
    latest: date | None,
    expected: date,
    status: str,
    backfill_attempted: bool = False,
    source: str = "postgresql",
) -> dict[str, Any]:
    """Serialize freshness without exposing provider internals."""
    return {
        "status": status,
        "latest_session": latest.isoformat() if latest else None,
        "expected_session": expected.isoformat(),
        "sessions_behind": weekday_sessions_between(latest, expected) if latest else None,
        "backfill_attempted": backfill_attempted,
        "source": source,
    }
