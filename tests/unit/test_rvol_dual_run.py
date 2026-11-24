"""RVOL v2 excludes the current candle; v1 remains available unchanged."""

import numpy as np

from app.indicators import Ohlcv
from app.indicators.registry import compute_indicator
from app.indicators.volume import compute_relative_volume, compute_relative_volume_previous


def ohlcv_with_last_volume(last: float) -> Ohlcv:
    volume = np.array([100.0] * 20 + [last], dtype=np.float64)
    close = np.full(len(volume), 10_000.0, dtype=np.float64)
    return Ohlcv(close, close, close, close, volume)


def test_v2_denominator_excludes_current_candle():
    v1 = compute_relative_volume(ohlcv_with_last_volume(152).volume, period=20).values["rvol"]
    v2 = compute_relative_volume_previous(ohlcv_with_last_volume(152).volume, period=20).values[
        "rvol"
    ]
    assert round(v2, 6) == 1.52
    assert round(v1, 6) < v2  # current candle inflates the v1 denominator


def test_dual_run_reports_classification_difference_at_threshold():
    data = ohlcv_with_last_volume(152)
    v1 = compute_indicator("rvol", data).values["rvol"]
    v2 = compute_indicator("rvol_v2", data).values["rvol"]
    assert v1 is not None and v2 is not None
    assert v1 < 1.5 <= v2
    assert compute_indicator("rvol", data).signal == "normal"
    assert compute_indicator("rvol_v2", data).signal == "above_average"


def test_v2_requires_full_previous_window():
    data = ohlcv_with_last_volume(152)
    short = Ohlcv(
        data.open[-10:], data.high[-10:], data.low[-10:], data.close[-10:], data.volume[-10:]
    )
    assert compute_indicator("rvol_v2", short).values["rvol"] is None
