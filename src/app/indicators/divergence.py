"""Phân kỳ giữa giá và RSI.

Chỉ dùng pivot đã xác nhận (pivot tại bar `i` chỉ được biết tại bar
`i + pivot_length`), nên không có dữ liệu tương lai. Hai pivot được so sánh phải
cách nhau không quá `max_gap_bars` nến.
"""

import numpy as np
import talib

from app.indicators import IndicatorOutput, as_float_array, last_value
from app.indicators.market_structure import find_pivot_highs, find_pivot_lows

DIVERGENCE_KEYS = (
    "bearish_regular",
    "bullish_regular",
    "bearish_hidden",
    "bullish_hidden",
)


def _compare_pairs(
    price_pivots: list[int],
    rsi_values: np.ndarray,
    prices: np.ndarray,
    *,
    max_gap_bars: int,
    is_high: bool,
) -> tuple[str | None, int | None]:
    """So sánh cặp pivot gần nhất để tìm phân kỳ.

    Args:
        price_pivots: Index các pivot giá đã xác nhận.
        rsi_values: Chuỗi RSI cùng độ dài với giá.
        prices: Chuỗi giá tại pivot (high hoặc low).
        max_gap_bars: Khoảng cách tối đa giữa hai pivot.
        is_high: True khi xét pivot high.

    Returns:
        Tuple (loại phân kỳ, số nến giữa hai pivot) hoặc (None, None).
    """
    usable = [index for index in price_pivots if not np.isnan(rsi_values[index])]
    if len(usable) < 2:
        return None, None

    first, second = usable[-2], usable[-1]
    gap = second - first
    if gap > max_gap_bars:
        return None, None

    price_first, price_second = float(prices[first]), float(prices[second])
    rsi_first, rsi_second = float(rsi_values[first]), float(rsi_values[second])

    if is_high:
        if price_second > price_first and rsi_second < rsi_first:
            return "bearish_regular", gap
        if price_second < price_first and rsi_second > rsi_first:
            return "bearish_hidden", gap
    else:
        if price_second < price_first and rsi_second > rsi_first:
            return "bullish_regular", gap
        if price_second > price_first and rsi_second < rsi_first:
            return "bullish_hidden", gap

    return None, gap


def compute_divergence(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = 14,
    pivot_length: int = 5,
    max_gap_bars: int = 30,
) -> IndicatorOutput:
    """Phát hiện phân kỳ thường và phân kỳ ẩn giữa giá và RSI.

    Args:
        high: Chuỗi giá cao nhất.
        low: Chuỗi giá thấp nhất.
        close: Chuỗi giá đóng cửa.
        rsi_period: Chu kỳ RSI dùng để so sánh.
        pivot_length: Số nến trái/phải khi xác nhận pivot.
        max_gap_bars: Khoảng cách tối đa giữa hai pivot được so sánh.

    Returns:
        `IndicatorOutput` với bốn cờ phân kỳ và nhãn `signal` là loại phân kỳ mới
        nhất; giá trị None khi chưa đủ dữ liệu.
    """
    parameters: dict[str, int | float | str] = {
        "rsi_period": rsi_period,
        "pivot_length": pivot_length,
        "max_gap_bars": max_gap_bars,
    }
    warmup = max(rsi_period + 1, pivot_length * 2 + 2)
    empty = IndicatorOutput(
        indicator="divergence",
        parameters=parameters,
        values=dict.fromkeys(DIVERGENCE_KEYS),
        warmup=warmup,
    )

    close_array = as_float_array(close)
    high_array = as_float_array(high)
    low_array = as_float_array(low)
    if close_array.size < warmup:
        return empty

    rsi_values = talib.RSI(close_array, timeperiod=rsi_period)
    high_pivots = find_pivot_highs(high_array, pivot_length, pivot_length)
    low_pivots = find_pivot_lows(low_array, pivot_length, pivot_length)

    bearish_type, high_gap = _compare_pairs(
        high_pivots, rsi_values, high_array, max_gap_bars=max_gap_bars, is_high=True
    )
    bullish_type, low_gap = _compare_pairs(
        low_pivots, rsi_values, low_array, max_gap_bars=max_gap_bars, is_high=False
    )

    values = dict.fromkeys(DIVERGENCE_KEYS, 0.0)
    signal = None

    candidates: list[tuple[int, str]] = []
    if bearish_type is not None and high_gap is not None:
        values[bearish_type] = 1.0
        candidates.append((high_gap, bearish_type))
    if bullish_type is not None and low_gap is not None:
        values[bullish_type] = 1.0
        candidates.append((low_gap, bullish_type))

    if candidates:
        candidates.sort()
        signal = candidates[0][1]

    return IndicatorOutput(
        indicator="divergence",
        parameters=parameters,
        values=values,
        warmup=warmup,
        signal=signal,
    )


def latest_rsi(close: np.ndarray, *, period: int = 14) -> float | None:
    """Giá trị RSI cuối cùng, dùng cho test và debug.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Chu kỳ RSI.

    Returns:
        Giá trị RSI cuối hoặc None khi chưa đủ dữ liệu.
    """
    return last_value(talib.RSI(as_float_array(close), timeperiod=period))
