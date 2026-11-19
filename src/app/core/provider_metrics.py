"""Context-local provider timings and usage; never stores prompts or credentials."""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

provider_events: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "provider_events", default=None
)
# LightRAG owns long-lived worker tasks created outside the request context. A
# module-level fallback lets those workers record metadata for the active job.
_active_events: list[dict[str, Any]] | None = None


@contextmanager
def collect_provider_events() -> Iterator[list[dict[str, Any]]]:
    """Collect provider calls for the current ingestion task and nested async tasks."""
    global _active_events
    events: list[dict[str, Any]] = []
    previous = _active_events
    _active_events = events
    token = provider_events.set(events)
    try:
        yield events
    finally:
        provider_events.reset(token)
        _active_events = previous


def record_provider_event(**event: Any) -> None:
    """Append a bounded metadata-only event when a collector is active."""
    events = provider_events.get()
    if events is None:
        events = _active_events
    if events is not None:
        events.append(event)


def _usage_number(usage: Any, key: str) -> float:
    if not isinstance(usage, dict):
        return 0.0
    value = usage.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def summarize_provider_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate request count, tokens and cost without duplicating wall-clock time."""
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"requests": 0.0, "duration_seconds": 0.0, "cost": 0.0}
    )
    total_cost = 0.0
    prompt_tokens = 0.0
    completion_tokens = 0.0
    errors = 0
    for event in events:
        model = str(event.get("model") or event.get("provider") or "unknown")
        usage = event.get("token_usage") or {}
        cost = float(
            event.get("estimated_cost")
            or (usage.get("cost") if isinstance(usage, dict) else 0)
            or 0
        )
        by_model[model]["requests"] += 1
        by_model[model]["duration_seconds"] += float(event.get("duration_seconds") or 0)
        by_model[model]["cost"] += cost
        total_cost += cost
        prompt_tokens += _usage_number(usage, "prompt_tokens")
        completion_tokens += _usage_number(usage, "completion_tokens")
        if event.get("status") not in (None, "ok"):
            errors += 1
    return {
        "requests": len(events),
        "errors": errors,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost": total_cost,
        "by_model": dict(sorted(by_model.items())),
    }
