"""Chỉ báo khối lượng và dòng tiền: Volume SMA, RVOL, OBV và MFI."""

import numpy as np

from app.indicators import IndicatorOutput, as_float_array, last_value
from app.indicators.native import native_series


def compute_volume_sma(volume: np.ndarray, *, period: int = 20) -> IndicatorOutput:
    """Tính trung bình khối lượng tại nến cuối.

    Args:
        volume: Chuỗi khối lượng.
        period: Số nến lấy trung bình.

    Returns:
        `IndicatorOutput` với khoá `volume_sma`.
    """
    values = native_series("SMA", as_float_array(volume), timeperiod=period)
    return IndicatorOutput(
        indicator="volume_sma",
        parameters={"period": period},
        values={"volume_sma": last_value(values)},
        warmup=period,
    )


def compute_relative_volume(volume: np.ndarray, *, period: int = 20) -> IndicatorOutput:
    """Tính Relative Volume: khối lượng hiện tại chia trung bình `period` nến.

    Args:
        volume: Chuỗi khối lượng.
        period: Số nến lấy trung bình.

    Returns:
        `IndicatorOutput` với `rvol`; None khi trung bình bằng 0 hoặc chưa đủ nến.
    """
    volume_array = as_float_array(volume)
    average = native_series("SMA", volume_array, timeperiod=period)
    average_value = last_value(average)
    current = last_value(volume_array)

    rvol = None
    if current is not None and average_value not in (None, 0.0):
        rvol = current / average_value

    signal = None
    if rvol is not None:
        signal = "above_average" if rvol >= 1.5 else "normal"

    return IndicatorOutput(
        indicator="rvol",
        parameters={"period": period},
        values={"rvol": rvol},
        warmup=period,
        signal=signal,
    )


def compute_relative_volume_previous(volume: np.ndarray, *, period: int = 20) -> IndicatorOutput:
    """RVOL v2: current volume / mean of the previous ``period`` completed sessions.

    The current candle is deliberately excluded from the denominator so the
    denominator is known before the session being measured. RVOL v1 remains
    available as ``rvol`` for backward compatibility.
    """
    volume_array = as_float_array(volume)
    if len(volume_array) < period + 1:
        return IndicatorOutput(
            indicator="rvol_v2",
            parameters={"period": period},
            values={"rvol": None},
            warmup=period + 1,
        )
    average_value = float(np.mean(volume_array[-period - 1 : -1]))
    current = float(volume_array[-1])
    rvol = current / average_value if np.isfinite(average_value) and average_value != 0 else None
    signal = None
    if rvol is not None:
        signal = "above_average" if rvol >= 1.5 else "below_average" if rvol < 0.8 else "normal"
    return IndicatorOutput(
        indicator="rvol_v2",
        parameters={"period": period},
        values={"rvol": rvol},
        warmup=period + 1,
        signal=signal,
    )


def compute_obv(close: np.ndarray, volume: np.ndarray) -> IndicatorOutput:
    """Tính On Balance Volume tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        volume: Chuỗi khối lượng.

    Returns:
        `IndicatorOutput` với khoá `obv`.
    """
    values = native_series("OBV", as_float_array(close), as_float_array(volume))
    return IndicatorOutput(
        indicator="obv",
        parameters={},
        values={"obv": last_value(values)},
        warmup=2,
    )


def compute_mfi(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    period: int = 14,
) -> IndicatorOutput:
    """Tính Money Flow Index tại nến cuối.

    MFI là RSI có trọng số khối lượng; giá trị cao/thấp chỉ mô tả trạng thái dòng
    tiền, không phải khuyến nghị giao dịch.

    Args:
        high: Chuỗi giá cao nhất.
        low: Chuỗi giá thấp nhất.
        close: Chuỗi giá đóng cửa.
        volume: Chuỗi khối lượng.
        period: Chu kỳ MFI.

    Returns:
        `IndicatorOutput` với khoá `mfi`.
    """
    values = native_series(
        "MFI",
        as_float_array(high),
        as_float_array(low),
        as_float_array(close),
        as_float_array(volume),
        timeperiod=period,
    )
    mfi_value = last_value(values)

    signal = None
    if mfi_value is not None:
        if mfi_value >= 80:
            signal = "overbought"
        elif mfi_value <= 20:
            signal = "oversold"
        else:
            signal = "neutral"

    return IndicatorOutput(
        indicator="mfi",
        parameters={"period": period},
        values={"mfi": mfi_value},
        warmup=period + 1,
        signal=signal,
    )
