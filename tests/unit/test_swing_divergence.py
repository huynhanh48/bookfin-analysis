"""Production swing detector, API evidence, causal replay and service integration."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.core.config import Settings
from app.indicators import Ohlcv
from app.indicators.divergence import compute_divergence
from app.indicators.registry import parse_request
from app.indicators.swing_divergence import (
    DivergenceConfig,
    detect_divergence,
    event_alerts,
    match_pivot,
    oscillator_context,
)
from app.services.indicator_service import IndicatorService


def fixture_series(first, second, *, high=True, length=18):
    """Two isolated price/oscillator extrema with ample matching confirmation bars."""
    values = np.full(length, 0.0 if high else 100.0)
    values[3], values[11] = first, second
    return values


def config(**kwargs):
    """Small pivots make exact confirmation timing visible in deterministic fixtures."""
    return DivergenceConfig(
        pivot_length=1, pivot_right=1, tolerance_bars=2, min_gap_bars=3, **kwargs
    )


@pytest.mark.parametrize(
    ("oscillator", "bearish"), [("MACD", True), ("MACD", False), ("RSI", True), ("RSI", False)]
)
def test_actual_four_required_regular_patterns(oscillator, bearish):
    prices = fixture_series(10, 12) if bearish else fixture_series(90, 88, high=False)
    osc = fixture_series(7, 5) if bearish else fixture_series(20, 25, high=False)
    absent = np.full(18, np.nan)
    events = detect_divergence(
        prices if bearish else absent,
        absent if bearish else prices,
        osc if oscillator == "MACD" else absent,
        osc if oscillator == "RSI" else absent,
        config(),
    )
    assert len(events) == 1
    event = events[0]
    assert event["direction"] == ("bearish" if bearish else "bullish")
    assert [o["indicator"] for o in event["oscillators"]] == [oscillator]
    assert (event["start_index"], event["end_index"], event["confirmed_index"]) == (3, 11, 14)
    assert event_alerts(event) == [f"{oscillator.lower()}_{event['direction']}_divergence"]


def test_full_history_matches_every_closed_prefix_and_forming_tail():
    price, osc = fixture_series(10, 12), fixture_series(7, 5)
    absent = np.full(18, np.nan)
    events = detect_divergence(price, absent, osc, osc, config())
    assert len(events[0]["oscillators"]) == 2
    assert len(event_alerts(events[0])) == 3
    for end in range(18):
        assert detect_divergence(price[:end], absent[:end], osc[:end], osc[:end], config()) == [
            e for e in events if e["confirmed_index"] < end
        ]
    assert not detect_divergence(price, absent, osc, osc, config(), closed_count=14)
    assert detect_divergence(price, absent, osc, osc, config(), closed_count=15) == events


def test_independent_sources_require_same_price_pair_to_combine():
    price, macd, rsi = np.zeros(26), np.zeros(26), np.zeros(26)
    for bar, p, m, r in [(3, 10, 7, 9), (11, 9, 8, 5), (19, 12, 6, 6)]:
        price[bar], macd[bar], rsi[bar] = p, m, r
    events = detect_divergence(price, np.full(26, np.nan), macd, rsi, config())
    assert [(e["start_index"], e["oscillators"][0]["indicator"]) for e in events] == [
        (11, "MACD"),
    ]
    assert all(len(event_alerts(event)) == 1 for event in events)


def test_nearest_matching_never_reuses_or_crosses_and_accepts_tolerance_edge():
    assert match_pivot(3, [2, 4], -1, 1) == 2
    assert match_pivot(3, [2, 4], 2, 1) == 4
    assert match_pivot(3, [2, 4], 4, 1) is None
    price, osc = fixture_series(10, 12), np.zeros(18)
    osc[5], osc[10] = 7, 5
    assert detect_divergence(price, np.full(18, np.nan), osc, osc, config())
    no_tolerance = DivergenceConfig(pivot_length=1, pivot_right=1, tolerance_bars=0, min_gap_bars=3)
    assert not detect_divergence(price, np.full(18, np.nan), osc, osc, no_tolerance)


def test_filters_and_invalid_data():
    price, osc, absent = fixture_series(10, 12), fixture_series(7, 5), np.full(18, np.nan)
    for options in (
        {"max_gap_bars": 7},
        {"min_price_percent": 21},
        {"min_macd_delta": 3, "min_rsi_delta": 3},
        {"enable_macd": 0, "enable_rsi": 0},
        {"enable_bearish": 0},
    ):
        assert not detect_divergence(price, absent, osc, osc, config(**options))
    assert not detect_divergence(fixture_series(10, 10), absent, osc, osc, config())
    assert not detect_divergence(
        price, absent, fixture_series(7, 7), fixture_series(7, 7), config()
    )
    assert not detect_divergence(price, absent, absent, absent, config())
    with pytest.raises(ValueError):
        detect_divergence(price, absent[:3], osc, osc, config())
    with pytest.raises(ValueError):
        parse_request("divergence:14:5:30:5:3:50")
    with pytest.raises(ValueError):
        DivergenceConfig(pivot_length=1.5)
    with pytest.raises(ValueError):
        DivergenceConfig(min_price_percent=float("nan"))


def install_oscillators(monkeypatch):
    """Keep detector real; mock TA-Lib only to control exact swing geometry."""
    price, osc = fixture_series(10, 12), fixture_series(7, 5)
    monkeypatch.setattr(
        "app.indicators.divergence.talib.RSI", lambda close, **kw: osc[: len(close)]
    )
    monkeypatch.setattr(
        "app.indicators.divergence.talib.MACD",
        lambda close, **kw: (osc[: len(close)], np.zeros(len(close)), -osc[: len(close)]),
    )
    return price, osc


def test_existing_indicator_outputs_only_confirmation_pulses_and_histogram(monkeypatch):
    price, _ = install_oscillators(monkeypatch)
    options = dict(pivot_length=1, pivot_right=1, tolerance_bars=2, min_gap_bars=3)
    confirmed = compute_divergence(price[:15], np.zeros(15), price[:15], **options)
    assert confirmed.values["bearish_regular"] == 1
    assert confirmed.signal == "bearish_regular"
    later = compute_divergence(price, np.zeros(18), price, **options)
    assert later.signal is None and later.values["bearish_regular"] == 0
    assert len(later.events) == 1
    histogram = compute_divergence(
        price, np.zeros(18), price, use_histogram=1, enable_rsi=0, **options
    )
    assert not histogram.events


def test_service_series_has_stable_events_and_confirmation_time(monkeypatch):
    price, _ = install_oscillators(monkeypatch)
    candles = [
        SimpleNamespace(
            candle_open_time=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=i),
            timeframe="1D",
            open=float(p),
            high=float(p),
            low=0.0,
            close=float(p),
            volume=100.0,
        )
        for i, p in enumerate(price)
    ]
    service = IndicatorService(
        settings=Settings(_env_file=None),
        market_repository=MagicMock(),
        indicator_repository=MagicMock(),
    )
    data = Ohlcv(price, price, np.zeros(18), price, np.ones(18))
    parameters = dict(pivot_length=1, pivot_right=1, tolerance_bars=2, min_gap_bars=3)
    result = service._build_series("divergence", parameters, data, candles, None)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.start_time == candles[3].candle_open_time
    assert event.end_time == candles[11].candle_open_time
    assert event.confirmed_at == candles[14].candle_open_time
    assert event.label == "MACD + RSI Bear"
    assert [p.time for p in result.points if p.signal] == [candles[14].candle_open_time]
    assert (
        service._build_series("divergence", parameters, data, candles, None).events[0].id
        == event.id
    )


async def test_named_api_validation_and_existing_series_contract(client):
    response = await client.get("/api/v1/indicators/FPT/divergence?min_gap_bars=30&max_gap_bars=5")
    assert response.status_code == 422
    response = await client.get("/api/v1/indicators/FPT/divergence?tolerance_bars=21")
    assert response.status_code == 422
    response = await client.get("/api/v1/indicators/FPT/series?indicator=divergence:14:5:30:5:3:50")
    assert response.status_code == 400


def test_service_excludes_daily_and_weekly_forming_bars(monkeypatch):
    from zoneinfo import ZoneInfo

    import app.services.indicator_service as module

    zone = ZoneInfo("Asia/Ho_Chi_Minh")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 18, 14, 59, tzinfo=zone).astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    service = IndicatorService(
        settings=Settings(_env_file=None),
        market_repository=MagicMock(),
        indicator_repository=MagicMock(),
    )
    yesterday = SimpleNamespace(
        candle_open_time=datetime(2026, 11, 17, tzinfo=zone), timeframe="1D"
    )
    today = SimpleNamespace(candle_open_time=datetime(2026, 11, 18, tzinfo=zone), timeframe="1D")
    this_week = SimpleNamespace(
        candle_open_time=datetime(2026, 11, 16, tzinfo=zone), timeframe="1W"
    )
    assert service._divergence_closed_candles([yesterday, today]) == [yesterday]
    assert service._divergence_closed_candles([this_week]) == []


async def test_named_divergence_api_passes_valid_configuration_to_service(client):
    from app.core.dependencies import get_indicator_service
    from app.main import app

    service = SimpleNamespace(
        get_series=AsyncMock(
            return_value=SimpleNamespace(
                symbol="FPT", timeframe="1D", as_of=None, candles=[], series=[]
            )
        )
    )
    app.dependency_overrides[get_indicator_service] = lambda: service
    try:
        response = await client.get(
            "/api/v1/indicators/FPT/divergence?pivot_length=3&pivot_right=4&tolerance_bars=2&use_histogram=1"
        )
        assert response.status_code == 200
        passed = service.get_series.call_args.kwargs["requests"][0][1]
        assert (
            passed["pivot_length"],
            passed["pivot_right"],
            passed["tolerance_bars"],
            passed["use_histogram"],
        ) == (3, 4, 2, 1)
    finally:
        app.dependency_overrides.clear()


def test_generic_indicator_line_reuses_engine_without_macd_or_rsi():
    from app.indicators.swing_divergence import IndicatorLine, SwingDivergenceDetector

    price, oscillator = fixture_series(10, 12), fixture_series(7, 5)
    events = SwingDivergenceDetector().detect(
        price, np.full(18, np.nan), [IndicatorLine("CUSTOM_MOMENTUM", oscillator)], config()
    )
    assert len(events) == 1
    assert events[0]["oscillators"][0]["indicator"] == "CUSTOM_MOMENTUM"


def test_no_skipping_intermediate_oscillator_extremum():
    price, osc = fixture_series(10, 12), fixture_series(7, 5)
    osc[7] = 8
    assert not detect_divergence(price, np.full(18, np.nan), osc, osc, config())


def test_obstructed_segment_is_rejected():
    from app.indicators.swing_divergence import unobstructed

    assert not unobstructed(np.array([10.0, 15.0, 12.0]), 0, 2, True)
    assert not unobstructed(np.array([10.0, 5.0, 8.0]), 0, 2, False)
    assert unobstructed(np.array([10.0, 9.0, 12.0]), 0, 2, True)


def test_dpg_reported_false_positive_regression():
    import json
    from pathlib import Path

    from app.indicators.divergence import divergence_history

    fixture = json.loads(Path("tests/fixtures/dpg_divergence_ohlcv.json").read_text())
    candles = fixture["candles"]
    assert len(candles) == 250
    arrays = [np.array([c[key] for c in candles], dtype=float) for key in ("high", "low", "close")]
    assert divergence_history(*arrays, DivergenceConfig()) == []


def test_decorator_and_functional_wrapper_use_real_detector():
    from app.indicators.divergence_decorator import phan_ky

    price, oscillator = fixture_series(10, 12), fixture_series(7, 5)
    data = Ohlcv(price, price, np.zeros(18), price, np.ones(18))

    @phan_ky(name="ABC")
    def abc(ohlcv, factor=1.0):
        return oscillator[: len(ohlcv)] * factor

    result = abc(data, factor=2, divergence_config=config())
    assert result.name == "ABC" and len(result.events) == 1
    assert result.events[0]["oscillators"][0]["start_value"] == 14
    assert not abc(data, closed_count=14, divergence_config=config()).events
    assert np.array_equal(result.values, oscillator * 2)

    def raw(ohlcv):
        return oscillator[: len(ohlcv)]

    assert phan_ky(raw)(data, divergence_config=config()).events
    with pytest.raises(ValueError):
        phan_ky(lambda o: np.zeros(3))(data)
    with pytest.raises(ValueError):
        phan_ky(lambda o: np.zeros((18, 2)))(data)


def test_generic_alert_does_not_invent_macd_rsi_combination():
    assert event_alerts(
        {"direction": "bullish", "oscillators": [{"indicator": "ABC"}, {"indicator": "OBV"}]}
    ) == ["abc_bullish_divergence", "obv_bullish_divergence"]


def test_rsi_context_records_window_extremes_without_requiring_breakout_extreme():
    values = np.array([80.0, 75.0, 78.0, 72.0, 68.0])
    context = oscillator_context("RSI", values, 0, 4)
    assert context == {
        "min": 68.0,
        "max": 80.0,
        "rsi_at_first_pivot": 80.0,
        "rsi_at_second_pivot": 68.0,
        "rsi_min_during_window": 68.0,
        "rsi_max_during_window": 80.0,
        "oversold_seen": False,
        "overbought_seen": True,
    }
    assert oscillator_context("MACD", values, 0, 4) is None
