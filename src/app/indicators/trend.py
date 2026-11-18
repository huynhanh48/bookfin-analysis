"""Chỉ báo xu hướng: SMA, EMA, ADX, +DI/-DI và Ichimoku."""

import numpy as np
import talib

from app.indicators import IndicatorOutput, as_float_array, last_value


def compute_sma(close: np.ndarray, *, period: int = 20) -> IndicatorOutput:
    """Tính Simple Moving Average tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Số nến của đường trung bình.

    Returns:
        `IndicatorOutput` với khoá `sma`.
    """
    values = talib.SMA(as_float_array(close), timeperiod=period)
    return IndicatorOutput(
        indicator="sma",
        parameters={"period": period},
        values={"sma": last_value(values)},
        warmup=period,
    )


def compute_ema(close: np.ndarray, *, period: int = 20) -> IndicatorOutput:
    """Tính Exponential Moving Average tại nến cuối.

    Args:
        close: Chuỗi giá đóng cửa.
        period: Số nến của đường trung bình.

    Returns:
        `IndicatorOutput` với khoá `ema`.
    """
    values = talib.EMA(as_float_array(close), timeperiod=period)
    return IndicatorOutput(
        indicator="ema",
        parameters={"period": period},
        values={"ema": last_value(values)},
        warmup=period,
    )


def compute_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = 14,
) -> IndicatorOutput:
    """Tính ADX và hai đường định hướng +DI/-DI.

    ADX đo sức mạnh xu hướng, không đo hướng; chỉ đọc kèm +DI/-DI mới suy ra
    hướng. Ngưỡng "trending" chỉ là mô tả trạng thái, không phải tín hiệu mua bán.

    Args:
        high: Chuỗi giá cao nhất.
        low: Chuỗi giá thấp nhất.
        close: Chuỗi giá đóng cửa.
        period: Số nến của ADX.

    Returns:
        `IndicatorOutput` với các khoá `adx`, `plus_di`, `minus_di`.
    """
    high_array = as_float_array(high)
    low_array = as_float_array(low)
    close_array = as_float_array(close)
    adx = talib.ADX(high_array, low_array, close_array, timeperiod=period)
    plus_di = talib.PLUS_DI(high_array, low_array, close_array, timeperiod=period)
    minus_di = talib.MINUS_DI(high_array, low_array, close_array, timeperiod=period)

    adx_value = last_value(adx)
    signal = None
    if adx_value is not None:
        signal = "trending" if adx_value >= 25 else "weak_trend"

    return IndicatorOutput(
        indicator="adx",
        parameters={"period": period},
        values={
            "adx": adx_value,
            "plus_di": last_value(plus_di),
            "minus_di": last_value(minus_di),
        },
        warmup=period * 2,
        signal=signal,
    )


def compute_ichimoku(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
) -> IndicatorOutput:
    """Tính Ichimoku Kinko Hyo tại nến cuối.

    Args:
        high: Chuỗi giá cao nhất.
        low: Chuỗi giá thấp nhất.
        close: Chuỗi giá đóng cửa.
        tenkan_period: Chu kỳ Tenkan-sen.
        kijun_period: Chu kỳ Kijun-sen.
        senkou_b_period: Chu kỳ Senkou Span B.

    Returns:
        `IndicatorOutput` với tenkan, kijun, hai đường mây và vị thế giá so với mây.
    """
    tenkan, kijun, senkou_a, senkou_b = talib.ICHIMOKU(
        as_float_array(high),
        as_float_array(low),
        tenkan_period,
        kijun_period,
        senkou_b_period,
    )
    tenkan_value = last_value(tenkan)
    kijun_value = last_value(kijun)
    senkou_a_value = last_value(senkou_a)
    senkou_b_value = last_value(senkou_b)
    price = last_value(close)

    signal = None
    if price is not None and senkou_a_value is not None and senkou_b_value is not None:
        cloud_top = max(senkou_a_value, senkou_b_value)
        cloud_bottom = min(senkou_a_value, senkou_b_value)
        if price > cloud_top:
            signal = "above_cloud"
        elif price < cloud_bottom:
            signal = "below_cloud"
        else:
            signal = "inside_cloud"

    return IndicatorOutput(
        indicator="ichimoku",
        parameters={
            "tenkan_period": tenkan_period,
            "kijun_period": kijun_period,
            "senkou_b_period": senkou_b_period,
        },
        values={
            "tenkan": tenkan_value,
            "kijun": kijun_value,
            "senkou_a": senkou_a_value,
            "senkou_b": senkou_b_value,
        },
        warmup=senkou_b_period + kijun_period,
        signal=signal,
    )
