"""Hàm tính chỉ báo thuần: không gọi HTTP, không truy cập database.

Mọi module trong package này nhận mảng numpy và trả `IndicatorOutput`. Nhờ vậy
engine chỉ báo chạy được trong worker, trong test và trong request mà không cần
session database hay kết nối mạng.
"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

ENGINE_VERSION = "ind-1.0.0"
IndicatorStatus = Literal["ok", "insufficient_history"]


@dataclass(frozen=True, slots=True)
class IndicatorOutput:
    """Kết quả một chỉ báo tại nến cuối cùng của chuỗi đầu vào.

    Attributes:
        indicator: Tên chỉ báo, ví dụ rsi.
        parameters: Tham số đã dùng khi tính.
        values: Giá trị từng dòng; None khi chưa đủ dữ liệu (warmup).
        warmup: Số nến tối thiểu cần có để chỉ báo có giá trị.
        signal: Nhãn mô tả trạng thái, không phải khuyến nghị giao dịch.
    """

    indicator: str
    parameters: dict[str, int | float | str]
    values: dict[str, float | None] = field(default_factory=dict)
    warmup: int = 0
    signal: str | None = None

    @property
    def status(self) -> IndicatorStatus:
        """Trạng thái tính toán: ok khi có ít nhất một giá trị không None."""
        return (
            "ok"
            if any(value is not None for value in self.values.values())
            else "insufficient_history"
        )


@dataclass(frozen=True, slots=True)
class Ohlcv:
    """Chuỗi OHLCV dạng mảng numpy, sắp xếp tăng dần theo thời gian."""

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        """Số nến trong chuỗi."""
        return int(self.close.shape[0])


def as_float_array(values: object) -> np.ndarray:
    """Chuyển dữ liệu đầu vào thành mảng float64 liên tục cho TA-Lib.

    Args:
        values: Danh sách, tuple hoặc mảng numpy.

    Returns:
        Mảng `float64` một chiều.
    """
    return np.ascontiguousarray(np.asarray(values, dtype=np.float64))


def last_value(series: object) -> float | None:
    """Lấy giá trị cuối cùng của một chuỗi chỉ báo.

    Args:
        series: Mảng numpy do TA-Lib trả về.

    Returns:
        Giá trị cuối cùng dưới dạng float, hoặc None khi NaN/Inf (warmup).
    """
    array = np.asarray(series, dtype=np.float64)
    if array.size == 0:
        return None
    value = float(array.reshape(-1)[-1])
    if np.isnan(value) or np.isinf(value):
        return None
    return value
