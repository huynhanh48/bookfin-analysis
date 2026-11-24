"""Screener engine: từng điều kiện AFL và các edge case dữ liệu."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.indicators.market_structure import CandleBar
from app.schemas.screener import ScreenerConditions
from app.services.screener_engine import (
    DivergenceEventSummary,
    ScreenerEngine,
    SymbolSnapshot,
)

BASE = datetime(2026, 1, 2, tzinfo=UTC)


def bar(index: int, *, close: float = 20_000.0, volume: float = 100_000) -> CandleBar:
    """Tạo nến xác định cho test."""
    return CandleBar(
        time=BASE + timedelta(days=index),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def snapshot(
    *,
    history_volumes: list[float] | None = None,
    history_closes: list[float] | None = None,
    close: float = 20_600.0,
    volume: float = 200_000,
) -> SymbolSnapshot:
    """Snapshot mặc định thỏa cả 5 điều kiện."""
    volumes = history_volumes or [100_000] * 10
    closes = history_closes or [20_000.0] * 10
    history = tuple(
        bar(index, close=closes[index], volume=volumes[index]) for index in range(10)
    )
    return SymbolSnapshot(
        symbol="FPT",
        history=history,
        current=bar(10, close=close, volume=volume),
        status="CONFIRMED",
        updated_at=BASE + timedelta(days=10),
    )


def conditions() -> ScreenerConditions:
    return ScreenerConditions()


def test_all_conditions_match_and_metrics_are_correct():
    match = ScreenerEngine().evaluate(snapshot(), conditions())

    assert match is not None
    assert match.symbol == "FPT"
    assert match.matched_conditions == ("DK1", "DK2", "DK3", "DK4", "DK6")
    assert match.price == 20_600.0
    assert match.volume == 200_000
    assert match.volume_sma == pytest.approx(100_000)
    assert match.relative_volume == pytest.approx(2.0)
    assert match.ma5 == pytest.approx((20_000 * 4 + 20_600) / 5)
    assert match.change_percent == pytest.approx(3.0)


def test_dk1_min_volume_boundary():
    assert ScreenerEngine().evaluate(snapshot(volume=100_001), conditions()) is not None
    assert ScreenerEngine().evaluate(snapshot(volume=100_000), conditions()) is None


def test_dk2_price_uses_vnd_units():
    only_dk2 = conditions()
    for name in ("min_volume", "relative_volume", "price_change", "price_vs_ma5"):
        getattr(only_dk2, name).enabled = False
    assert ScreenerEngine().evaluate(snapshot(close=10_001), only_dk2) is not None
    assert ScreenerEngine().evaluate(snapshot(close=10_000), only_dk2) is None


def test_dk3_uses_only_previous_ten_volumes():
    # 79.5k < 0.8 * 100k = 80k nên phải fail. Nếu tính cả current vào SMA,
    # ngưỡng chỉ còn ~78.5k và điều kiện sẽ pass sai.
    match = ScreenerEngine().evaluate(snapshot(volume=79_500), conditions())
    assert match is None


def test_dk4_requires_more_than_two_percent():
    only_dk4 = conditions()
    for name in ("min_volume", "min_price", "relative_volume", "price_vs_ma5"):
        getattr(only_dk4, name).enabled = False
    assert ScreenerEngine().evaluate(snapshot(close=20_401), only_dk4) is not None
    assert ScreenerEngine().evaluate(snapshot(close=20_400), only_dk4) is None


def test_dk6_ma5_includes_current_close():
    # MA5 gồm current = (4*20k + 20.46k)/5 = 20.092k; ngưỡng 20.49384k.
    # Nếu loại current khỏi MA5, ngưỡng 20.4k và 20.46k sẽ pass sai.
    match = ScreenerEngine().evaluate(snapshot(close=20_460), conditions())
    assert match is None


def test_each_toggle_can_disable_condition():
    custom = conditions()
    custom.min_volume.enabled = False
    custom.min_price.enabled = False
    custom.relative_volume.enabled = False
    custom.price_change.enabled = False
    custom.price_vs_ma5.enabled = False
    # Không có rule nào enabled: không coi là match toàn bộ.
    assert ScreenerEngine().evaluate(snapshot(), custom) is None

    custom = conditions()
    custom.min_volume.enabled = False
    custom.min_price.enabled = False
    custom.relative_volume.enabled = False
    custom.price_change.enabled = False
    custom.price_vs_ma5.enabled = True
    match = ScreenerEngine().evaluate(snapshot(close=20_600), custom)
    assert match is not None
    assert match.matched_conditions == ("DK6",)


def test_insufficient_history_and_invalid_data_are_skipped():
    short = SymbolSnapshot(
        symbol="FPT",
        history=tuple(bar(index) for index in range(4)),
        current=bar(4),
        status="CONFIRMED",
        updated_at=BASE,
    )
    assert ScreenerEngine().evaluate(short, conditions()) is None
    assert ScreenerEngine().evaluate(snapshot(volume=-1), conditions()) is None
    assert ScreenerEngine().evaluate(snapshot(close=0), conditions()) is None


def test_metrics_are_still_computed_when_conditions_disabled():
    custom = conditions()
    custom.relative_volume.enabled = False
    custom.price_vs_ma5.enabled = False
    match = ScreenerEngine().evaluate(snapshot(), custom)
    assert match is not None
    assert match.volume_sma == pytest.approx(100_000)
    assert match.ma5 == pytest.approx((20_000 * 4 + 20_600) / 5)
    assert match.matched_conditions == ("DK1", "DK2", "DK4")


def test_divergence_filter_matches_confirmed_event():
    custom = conditions()
    custom.divergence.enabled = True
    custom.divergence.direction = "bullish"
    custom.divergence.kind = "regular"
    event = DivergenceEventSummary(
        kind="regular",
        direction="bullish",
        indicators=("MACD", "RSI"),
        end_index=7,
        confirmed_index=9,
    )
    base = replace(snapshot(), divergence_events=(event,))
    match = ScreenerEngine().evaluate(base, custom)
    assert match is not None
    assert "DIVERGENCE" in match.matched_conditions
    assert match.divergence == event
    assert match.divergence.indicators == ("MACD", "RSI")

    custom.divergence.direction = "bearish"
    assert ScreenerEngine().evaluate(base, custom) is None


def test_custom_conditions_are_applied():
    custom = conditions()
    custom.min_volume.value = 500_000
    assert ScreenerEngine().evaluate(snapshot(volume=200_000), custom) is None
    custom.min_volume.enabled = False
    assert ScreenerEngine().evaluate(snapshot(volume=200_000), custom) is not None


def test_divergence_basis_uses_pivot_or_confirmation(monkeypatch):
    import app.services.screener_engine as module

    bars = [bar(index, close=20_000) for index in range(30)]
    event = {
        "kind": "regular",
        "direction": "bullish",
        "end_index": 22,
        "confirmed_index": 28,
        "oscillators": [{"indicator": "RSI"}],
    }
    monkeypatch.setattr(module, "divergence_history", lambda *args, **kwargs: [event])

    pivot_events = module.extract_divergence_events(
        bars, days=5, basis="pivot"
    )
    confirmed_events = module.extract_divergence_events(
        bars, days=5, basis="confirmed"
    )

    # Pivot cách hiện tại 7 phiên nên bị loại; nến xác nhận chỉ cách 1 phiên.
    assert pivot_events == ()
    assert len(confirmed_events) == 1
    assert confirmed_events[0].end_index == 22
    assert confirmed_events[0].confirmed_index == 28
