"""Ichimoku must use the cloud plotted at the price bar, not future spans."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.indicators import Ohlcv
from app.indicators.engine import IndicatorFrame
from app.indicators.factory import build_indicator, interpret_signal

PARAMS = {"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52}


def frame(n: int = 180) -> tuple[IndicatorFrame, np.ndarray, np.ndarray]:
    t = np.arange(n, dtype=np.float64)
    close = 100 + 0.35 * t + 6 * np.sin(t / 8)
    high = close + 1.5
    low = close - 1.5
    open_ = close - 0.3
    volume = np.full(n, 1_000_000.0)
    timestamps = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    return IndicatorFrame(Ohlcv(open_, high, low, close, volume), timestamps), close, high


def test_senkou_cloud_is_shifted_to_price_bar():
    data, _, _ = frame()
    result = build_indicator("ichimoku", PARAMS).calculate(data)
    k = PARAMS["kijun_period"]
    for index in range(k, len(data.timestamps)):
        assert result.series["cloud_a"][index] == pytest.approx(
            result.series["senkou_a"][index - k], nan_ok=True
        )
        assert result.series["cloud_b"][index] == pytest.approx(
            result.series["senkou_b"][index - k], nan_ok=True
        )


def test_cloud_at_latest_bar_is_causal_with_prefix_data():
    data, _, _ = frame()
    full = build_indicator("ichimoku", PARAMS).calculate(data)
    for end in (120, len(data.timestamps) - 1):
        prefix = IndicatorFrame(
            Ohlcv(
                data.ohlcv.open[: end + 1],
                data.ohlcv.high[: end + 1],
                data.ohlcv.low[: end + 1],
                data.ohlcv.close[: end + 1],
                data.ohlcv.volume[: end + 1],
            ),
            data.timestamps[: end + 1],
        )
        partial = build_indicator("ichimoku", PARAMS).calculate(prefix)
        assert partial.series["cloud_a"][-1] == pytest.approx(
            full.series["cloud_a"][end], nan_ok=True
        )
        assert partial.series["cloud_b"][-1] == pytest.approx(
            full.series["cloud_b"][end], nan_ok=True
        )


def test_raw_ichimoku_series_match_talib():
    import talib

    data, _, _ = frame()
    result = build_indicator("ichimoku", PARAMS).calculate(data)
    direct = talib.ICHIMOKU(data.ohlcv.high, data.ohlcv.low, 9, 26, 52)
    for key, expected in zip(("tenkan", "kijun", "senkou_a", "senkou_b"), direct, strict=True):
        np.testing.assert_allclose(result.series[key], expected, equal_nan=True)


def test_signal_uses_shifted_cloud_not_raw_span():
    values = {
        "tenkan": 61_350,
        "kijun": 61_450,
        "senkou_a": 61_400,
        "senkou_b": 65_750,
        "cloud_a": 65_375,
        "cloud_b": 70_250,
    }
    assert interpret_signal("ichimoku", values, {}, 61_500.0) == "below_cloud"


def test_raw_span_alone_still_supported_for_legacy_values():
    values = {"senkou_a": 61_400, "senkou_b": 65_750}
    assert interpret_signal("ichimoku", values, {}, 61_500.0) == "inside_cloud"
