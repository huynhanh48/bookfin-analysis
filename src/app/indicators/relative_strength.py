"""Sức mạnh tương đối của cổ phiếu so với chỉ số tham chiếu.

Hai chuỗi đầu vào phải đã được căn theo cùng phiên giao dịch. Việc căn phiên là
trách nhiệm của service vì cần dữ liệu thời gian; module này chỉ tính toán thuần.
"""

import numpy as np

from app.indicators import IndicatorOutput, as_float_array
from app.indicators.native import native_series


def compute_relative_strength(
    stock_close: np.ndarray,
    benchmark_close: np.ndarray,
    *,
    period: int = 20,
) -> IndicatorOutput:
    """Tính hiệu suất cổ phiếu so với chỉ số trong `period` phiên.

    Args:
        stock_close: Giá đóng cửa của cổ phiếu, đã căn phiên với chỉ số.
        benchmark_close: Giá đóng cửa của chỉ số tham chiếu.
        period: Số phiên dùng để tính hiệu suất.

    Returns:
        `IndicatorOutput` với hiệu suất cổ phiếu, hiệu suất chỉ số, mức vượt trội
        theo điểm phần trăm và tỷ lệ đường RS.
    """
    stock = as_float_array(stock_close)
    benchmark = as_float_array(benchmark_close)

    if stock.size != benchmark.size or stock.size <= period:
        return IndicatorOutput(
            indicator="relative_strength",
            parameters={"period": period},
            values={
                "stock_return_pct": None,
                "benchmark_return_pct": None,
                "outperformance_pct": None,
                "rs_ratio": None,
            },
            warmup=period + 1,
        )

    stock_start = float(stock[-period - 1])
    benchmark_start = float(benchmark[-period - 1])
    if stock_start == 0 or benchmark_start == 0:
        return IndicatorOutput(
            indicator="relative_strength",
            parameters={"period": period},
            values={
                "stock_return_pct": None,
                "benchmark_return_pct": None,
                "outperformance_pct": None,
                "rs_ratio": None,
            },
            warmup=period + 1,
        )

    stock_return = float(native_series("ROC", stock, timeperiod=period)[-1])
    benchmark_return = float(native_series("ROC", benchmark, timeperiod=period)[-1])
    outperformance = stock_return - benchmark_return

    rs_line = stock / benchmark
    rs_ratio = None
    if rs_line.size > period:
        rs_start = float(rs_line[-period - 1])
        if rs_start != 0:
            rs_ratio = float(rs_line[-1]) / rs_start

    signal = None
    if outperformance > 0:
        signal = "outperform"
    elif outperformance < 0:
        signal = "underperform"
    else:
        signal = "inline"

    return IndicatorOutput(
        indicator="relative_strength",
        parameters={"period": period},
        values={
            "stock_return_pct": stock_return,
            "benchmark_return_pct": benchmark_return,
            "outperformance_pct": outperformance,
            "rs_ratio": rs_ratio,
        },
        warmup=period + 1,
        signal=signal,
    )
