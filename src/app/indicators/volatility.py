"""Chỉ báo biến động: Bollinger Bands, BB Width và ATR."""

import numpy as np
import talib

from app.indicators import IndicatorOutput, as_float_array, last_value


def compute_bollinger(
    close: np.ndarray,
    *,
    period: int = 20,
    stddev: float = 2.0,
) -> IndicatorOutput:
    """Tính Bollinger Bands tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Chu kỳ trung bình và độ lệch chuẩn.
        stddev: Số độ lệch chuẩn cho hai dải.

    Returns:
        `IndicatorOutput` với `upper`, `middle`, `lower` và `percent_b`.
    """
    close_array = as_float_array(close)
    upper, middle, lower = talib.BBANDS(
        close_array,
        timeperiod=period,
        nbdevup=stddev,
        nbdevdn=stddev,
        matype=0,
    )
    upper_value = last_value(upper)
    middle_value = last_value(middle)
    lower_value = last_value(lower)
    price = last_value(close_array)

    percent_b = None
    if price is not None and upper_value is not None and lower_value is not None:
        band = upper_value - lower_value
        if band > 0:
            percent_b = (price - lower_value) / band

    return IndicatorOutput(
        indicator="bollinger",
        parameters={"period": period, "stddev": stddev},
        values={
            "upper": upper_value,
            "middle": middle_value,
            "lower": lower_value,
            "percent_b": percent_b,
        },
        warmup=period,
    )


def compute_bb_width(
    close: np.ndarray,
    *,
    period: int = 20,
    stddev: float = 2.0,
) -> IndicatorOutput:
    """Tính độ rộng dải Bollinger: (upper - lower) / middle.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Chu kỳ trung bình và độ lệch chuẩn.
        stddev: Số độ lệch chuẩn cho hai dải.

    Returns:
        `IndicatorOutput` với khoá `bb_width`.
    """
    close_array = as_float_array(close)
    upper, middle, lower = talib.BBANDS(
        close_array,
        timeperiod=period,
        nbdevup=stddev,
        nbdevdn=stddev,
        matype=0,
    )
    upper_value = last_value(upper)
    middle_value = last_value(middle)
    lower_value = last_value(lower)

    width = None
    if upper_value is not None and middle_value not in (None, 0.0) and lower_value is not None:
        width = (upper_value - lower_value) / middle_value

    return IndicatorOutput(
        indicator="bb_width",
        parameters={"period": period, "stddev": stddev},
        values={"bb_width": width},
        warmup=period,
    )


def compute_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = 14,
) -> IndicatorOutput:
    """Tính Average True Range tại nến cuối.

    ATR dùng để so sánh biên độ giữa các mã và để đo độ vượt ngưỡng khi phá cấu
    trúc; giá trị ATR tuyệt đối không so sánh được giữa các mã khác giá.

    Args:
        high: Chuỗi giá cao nhất.
        low: Chuỗi giá thấp nhất.
        close: Chuỗi giá đóng cửa.
        period: Chu kỳ ATR.

    Returns:
        `IndicatorOutput` với khoá `atr`.
    """
    values = talib.ATR(
        as_float_array(high),
        as_float_array(low),
        as_float_array(close),
        timeperiod=period,
    )
    return IndicatorOutput(
        indicator="atr",
        parameters={"period": period},
        values={"atr": last_value(values)},
        warmup=period + 1,
    )
