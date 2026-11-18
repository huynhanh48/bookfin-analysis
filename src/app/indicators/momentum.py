"""Chỉ báo động lượng: RSI, MACD và ROC."""

import numpy as np
import talib

from app.indicators import IndicatorOutput, as_float_array, last_value


def compute_rsi(close: np.ndarray, *, period: int = 14) -> IndicatorOutput:
    """Tính Relative Strength Index tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Chu kỳ RSI.

    Returns:
        `IndicatorOutput` với khoá `rsi`; `signal` chỉ mô tả vùng quá mua/quá bán
        theo ngưỡng tham chiếu, không phải khuyến nghị giao dịch.
    """
    values = talib.RSI(as_float_array(close), timeperiod=period)
    rsi_value = last_value(values)

    signal = None
    if rsi_value is not None:
        if rsi_value >= 70:
            signal = "overbought"
        elif rsi_value <= 30:
            signal = "oversold"
        else:
            signal = "neutral"

    return IndicatorOutput(
        indicator="rsi",
        parameters={"period": period},
        values={"rsi": rsi_value},
        warmup=period + 1,
        signal=signal,
    )


def compute_macd(
    close: np.ndarray,
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> IndicatorOutput:
    """Tính MACD, đường tín hiệu và histogram tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        fast_period: Chu kỳ EMA nhanh.
        slow_period: Chu kỳ EMA chậm.
        signal_period: Chu kỳ EMA của đường tín hiệu.

    Returns:
        `IndicatorOutput` với `macd`, `signal_line`, `histogram` và nhãn giao cắt.
    """
    close_array = as_float_array(close)
    macd, signal_line, histogram = talib.MACD(
        close_array,
        fastperiod=fast_period,
        slowperiod=slow_period,
        signalperiod=signal_period,
    )
    macd_value = last_value(macd)
    signal_value = last_value(signal_line)
    histogram_value = last_value(histogram)

    previous_histogram = None
    histogram_array = np.asarray(histogram, dtype=np.float64)
    if histogram_array.size >= 2 and not np.isnan(histogram_array[-2]):
        previous_histogram = float(histogram_array[-2])

    signal = None
    if histogram_value is not None and previous_histogram is not None:
        if histogram_value > 0 and previous_histogram <= 0:
            signal = "bullish_cross"
        elif histogram_value < 0 and previous_histogram >= 0:
            signal = "bearish_cross"
        elif histogram_value > 0:
            signal = "above_signal"
        else:
            signal = "below_signal"

    return IndicatorOutput(
        indicator="macd",
        parameters={
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": signal_period,
        },
        values={
            "macd": macd_value,
            "signal_line": signal_value,
            "histogram": histogram_value,
        },
        warmup=slow_period + signal_period,
        signal=signal,
    )


def compute_roc(close: np.ndarray, *, period: int = 12) -> IndicatorOutput:
    """Tính Rate of Change theo phần trăm tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Số nến so sánh.

    Returns:
        `IndicatorOutput` với khoá `roc`.
    """
    values = talib.ROC(as_float_array(close), timeperiod=period)
    return IndicatorOutput(
        indicator="roc",
        parameters={"period": period},
        values={"roc": last_value(values)},
        warmup=period + 1,
    )
