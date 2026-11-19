"""Native reference, fixed golden data, validation, finality and streaming parity."""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import talib
from numpy.testing import assert_allclose

from app.indicators import Ohlcv
from app.indicators.engine import IndicatorEngine, IndicatorFrame, IndicatorRegistry, TalibIndicator
from app.indicators.factory import NATIVE_SPECS, build_indicator, default_registry
from app.indicators.registry import INDICATORS, compute_indicator
from app.indicators.streaming import ConfirmedStream


@pytest.fixture
def frame():
    rows = json.loads(Path("tests/fixtures/dpg_divergence_ohlcv.json").read_text())["candles"]
    return IndicatorFrame(
        Ohlcv(
            *(
                np.array([c[k] for c in rows], dtype=np.float64)
                for k in ("open", "high", "low", "close", "volume")
            )
        ),
        [datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in rows],
    )


@pytest.mark.parametrize("name", list(NATIVE_SPECS))
def test_native_adapter_matches_direct_function(frame, name):
    params = INDICATORS[name].default_parameters
    adapter = build_indicator(name, params)
    result = adapter.calculate(frame)
    direct = getattr(talib, adapter.function)(
        *(getattr(frame.ohlcv, k) for k in adapter.required_inputs), **adapter.parameters
    )
    outputs = direct if isinstance(direct, tuple) else (direct,)
    for actual, expected in zip(result.series.values(), outputs, strict=True):
        assert actual.dtype == np.float64 or np.issubdtype(actual.dtype, np.integer)
        assert_allclose(actual, expected, rtol=1e-12, atol=1e-10, equal_nan=True)
    assert result.lookback >= 0


def test_legacy_golden_values_preserved(frame):
    old = json.loads(Path("docs/talib-migration/legacy-golden.json").read_text())
    for name, expected in old.items():
        actual = compute_indicator(name, frame.ohlcv, benchmark=frame.ohlcv.close * 1.1)
        for key, value in expected["values"].items():
            if value is None:
                assert actual.values[key] is None
            else:
                assert_allclose(actual.values[key], value, rtol=1e-10, atol=1e-9)


def test_reject_duplicate_out_of_order_missing_and_invalid_bars(frame):
    from dataclasses import replace

    for times in ([frame.timestamps[0]] * len(frame.timestamps), list(reversed(frame.timestamps))):
        with pytest.raises(ValueError):
            replace(frame, timestamps=times).validate()
    bad = frame.ohlcv.high.copy()
    bad[0] = np.nan
    with pytest.raises(ValueError):
        replace(frame, ohlcv=replace(frame.ohlcv, high=bad)).validate()
    bad = frame.ohlcv.volume.copy()
    bad[0] = -1
    with pytest.raises(ValueError):
        replace(frame, ohlcv=replace(frame.ohlcv, volume=bad)).validate()
    bad = frame.ohlcv.high.copy()
    bad[0] = 0
    with pytest.raises(ValueError):
        replace(frame, ohlcv=replace(frame.ohlcv, high=bad)).validate()


def test_confirmed_provisional_prefix_and_insufficient(frame):
    from dataclasses import replace

    engine = IndicatorEngine(default_registry())
    selected = replace(frame, closed_count=10)
    final = engine.calculate(selected, [("rsi", {})])[0]
    assert len(final.series["rsi"]) == 10 and final.status == "insufficient_history"
    preview = engine.calculate(selected, [("rsi", {})], provisional=True)[0]
    assert preview.provisional and len(preview.series["rsi"]) == len(frame.ohlcv)
    assert not final.provisional


def test_streaming_parity_peek_duplicate_and_correction(frame):
    from dataclasses import replace

    history = replace(frame, closed_count=len(frame.ohlcv) - 1).prefix()
    adapter = TalibIndicator("rsi", "RSI", {"timeperiod": 14}, ["rsi"])
    stream = ConfirmedStream(adapter, history)
    before = stream.handle.value
    timestamp = frame.timestamps[-1]
    bar = {"close": float(frame.ohlcv.close[-1])}
    provisional = stream.update(timestamp, bar, final=False)
    assert stream.handle.value == before
    final = stream.update(timestamp, bar)
    assert_allclose(final, talib.RSI(frame.ohlcv.close, timeperiod=14)[-1], rtol=1e-12)
    assert final == provisional == stream.update(timestamp, bar)
    with pytest.raises(ValueError):
        stream.update(timestamp, {"close": bar["close"] + 1})
    with pytest.raises(ValueError):
        stream.update(frame.timestamps[0], bar)


def test_unstable_period_is_restored(frame):
    before = talib.get_unstable_period("RSI")
    adapter = TalibIndicator("rsi", "RSI", {"timeperiod": 14}, ["rsi"], unstable={"RSI": 5})
    result = adapter.calculate(frame)
    assert result.lookback >= 14
    assert talib.get_unstable_period("RSI") == before


def test_registry_extension_and_duplicate_guard(frame):
    registry = IndicatorRegistry()
    registry.register("abc", lambda: TalibIndicator("abc", "WMA", {"timeperiod": 17}, ["abc"]))
    result = IndicatorEngine(registry).calculate(frame, [("abc", {})])[0]
    assert_allclose(
        result.series["abc"], talib.WMA(frame.ohlcv.close, timeperiod=17), equal_nan=True
    )
    with pytest.raises(ValueError):
        registry.register("abc", lambda: None)


def test_composites_match_native_reference(frame):
    data = frame.ohlcv
    adx = build_indicator("adx", {"period": 14}).calculate(frame)
    for key, function in [
        ("adx", talib.ADX),
        ("plus_di", talib.PLUS_DI),
        ("minus_di", talib.MINUS_DI),
    ]:
        assert_allclose(
            adx.series[key],
            function(data.high, data.low, data.close, timeperiod=14),
            equal_nan=True,
        )
    bands = build_indicator("bollinger", {"period": 20, "stddev": 2.0}).calculate(frame)
    reference = talib.BBANDS(data.close, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
    for key, expected in zip(["upper", "middle", "lower"], reference, strict=True):
        assert_allclose(bands.series[key], expected, equal_nan=True)
    legacy = compute_indicator("rvol", data).values["rvol"]
    assert_allclose(legacy, data.volume[-1] / talib.SMA(data.volume, timeperiod=20)[-1])
    # Native RVOL deliberately remains a separately named formula.
    assert_allclose(
        compute_indicator("rvol_native", data).values["rvol"],
        talib.RVOL(data.volume, timeperiod=20)[-1],
    )


def test_missing_input_never_becomes_zero(frame):
    from app.indicators.native import native_series

    data = frame.ohlcv.close.copy()
    data[50] = np.nan
    actual = native_series("SMA", data, timeperiod=20)
    assert_allclose(actual, talib.SMA(data, timeperiod=20), equal_nan=True)
    assert np.isnan(actual[50:]).all()


def test_asymmetric_swing_confirmation_and_append_only_events():
    from dataclasses import replace

    from tests.unit.test_market_structure import zigzag_bars

    from app.indicators.market_structure import MarketStructureConfig, analyze_structure

    bars = zigzag_bars()
    config = MarketStructureConfig(left_bars=2, right_bars=4)
    full = analyze_structure(bars, config=config)
    for cutoff in range(1, len(bars)):
        partial = analyze_structure(bars[:cutoff], config=config)
        for swing in partial.swings:
            assert swing.confirmed_at == bars[swing.index + 4].time
            assert swing.index + 4 < cutoff
        assert list(partial.events) == [
            event for event in full.events if event.broken_at <= bars[cutoff - 1].time
        ]
    enhanced = replace(config, mode="enhanced", min_volume_ratio=2.0, volume_period=3)
    # Fixed fixture has constant volume, so it cannot confirm 2x-volume breakouts.
    assert not analyze_structure(bars, config=enhanced).events


async def test_worker_recompute_only_closed_bars_and_reuses_same_inputs(frame, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from zoneinfo import ZoneInfo

    import app.services.indicator_service as module
    from app.core.config import Settings
    from app.services.indicator_service import IndicatorService

    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    last_day = frame.timestamps[-1].astimezone(zone).date()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(last_day.year, last_day.month, last_day.day, 14, 59, tzinfo=zone).astimezone(
                tz
            )

    monkeypatch.setattr(module, "datetime", Clock)
    candles = [
        SimpleNamespace(
            candle_open_time=t,
            timeframe="1D",
            **{
                k: float(getattr(frame.ohlcv, k)[i])
                for k in ("open", "high", "low", "close", "volume")
            },
        )
        for i, t in enumerate(frame.timestamps)
    ]
    saved = {}

    async def upsert(**row):
        saved[(row["engine_version"], row["indicator"], row["params_hash"])] = SimpleNamespace(
            **row
        )

    async def read(*args, engine_version):
        return [row for row in saved.values() if row.engine_version == engine_version]

    async def latest(*args, indicator, engine_version):
        rows = [
            row
            for row in saved.values()
            if row.engine_version == engine_version and row.indicator == indicator
        ]
        return rows[-1] if rows else None

    repository = SimpleNamespace(
        upsert_snapshot=AsyncMock(side_effect=upsert),
        get_snapshots=AsyncMock(side_effect=read),
        get_latest_snapshot=AsyncMock(side_effect=latest),
        upsert_structure_events=AsyncMock(),
    )
    market = SimpleNamespace(get_candles=AsyncMock(return_value=candles))
    service = IndicatorService(
        settings=Settings(_env_file=None), market_repository=market, indicator_repository=repository
    )
    first = await service.recompute("DPG")
    assert first.as_of == candles[-2].candle_open_time
    assert first.candles_used == len(candles) - 1
    calls = repository.upsert_snapshot.call_count
    second = await service.recompute("DPG")
    assert second.results == first.results
    assert repository.upsert_snapshot.call_count == calls, [
        call.kwargs["indicator"] for call in repository.upsert_snapshot.call_args_list[calls:]
    ]
    for row in saved.values():
        assert row.candle_time < candles[-1].candle_open_time
        assert row.payload["ta_lib_version"] == talib.__version__
        assert row.payload["formula_version"] == "talib-foundation-1"
    # Correction of a closed bar invalidates the input fingerprint.
    candles[-2].volume += 10
    await service.recompute("DPG")
    assert repository.upsert_snapshot.call_count > calls


def test_unstable_configuration_rejects_unsupported_or_deprecated():
    from app.indicators.native import configure_unstable_periods

    for config in ({"SMA": 5}, {"MFI": 3}, {"RSI": -1}):
        with pytest.raises(ValueError):
            configure_unstable_periods(config)


def test_series_keeps_existing_api_key(frame):
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.services.indicator_service import IndicatorService

    candles = [SimpleNamespace(candle_open_time=t, timeframe="1D") for t in frame.timestamps]
    service = IndicatorService(
        settings=Settings(_env_file=None), market_repository=None, indicator_repository=None
    )
    series = service._build_series("sma", {"period": 20}, frame.ohlcv, candles, None)
    assert series.key == "sma(period=20)"
    assert series.points[-1].time == frame.timestamps[-1]


def test_replay_cutoff_excludes_same_day_final_values_not_yet_known(frame):
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from app.core.config import Settings
    from app.services.indicator_service import IndicatorService

    service = IndicatorService(
        settings=Settings(_env_file=None), market_repository=None, indicator_repository=None
    )
    candles = [SimpleNamespace(candle_open_time=t, timeframe="1D") for t in frame.timestamps]
    last = candles[-1].candle_open_time.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    before = last.replace(hour=14, minute=59)
    after = last.replace(hour=15, minute=1)
    assert service._divergence_closed_candles(candles, as_of=before)[-1] == candles[-2]
    assert service._divergence_closed_candles(candles, as_of=after)[-1] == candles[-1]


@pytest.mark.parametrize("period", [0, -1, 1, 1.5, float("nan")])
def test_invalid_native_parameter_rejected_before_calculation(period):
    with pytest.raises(ValueError):
        TalibIndicator("rsi", "RSI", {"timeperiod": period})
