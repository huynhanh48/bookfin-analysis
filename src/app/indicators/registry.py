"""Đăng ký chỉ báo: ánh xạ tên chỉ báo sang hàm tính và tham số mặc định.

Registry là điểm mở rộng duy nhất khi thêm chỉ báo mới: khai báo một
`IndicatorSpec`, phần còn lại của hệ thống (service, worker, API) tự dùng được.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from app.indicators import (
    IndicatorOutput,
    Ohlcv,
    divergence,
    momentum,
    relative_strength,
    trend,
    volatility,
    volume,
)

InputKind = Literal["close", "volume", "cv", "hlc", "hlcv", "benchmark"]


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """Khai báo một chỉ báo.

    Attributes:
        name: Tên chỉ báo, cũng là khoá trong registry.
        category: Nhóm chỉ báo: trend, momentum, volume, volatility, relative.
        compute: Hàm tính thuần nhận mảng numpy.
        input_kind: Dữ liệu đầu vào hàm tính cần.
        default_parameters: Tham số mặc định.
        warmup: Số nến tối thiểu để có giá trị.
        description: Mô tả ngắn dùng cho endpoint liệt kê chỉ báo.
    """

    name: str
    category: str
    compute: Callable[..., IndicatorOutput]
    input_kind: InputKind
    default_parameters: dict[str, int | float] = field(default_factory=dict)
    warmup: int = 0
    description: str = ""


INDICATORS: dict[str, IndicatorSpec] = {
    "sma": IndicatorSpec(
        name="sma",
        category="trend",
        compute=trend.compute_sma,
        input_kind="close",
        default_parameters={"period": 20},
        warmup=20,
        description="Đường trung bình động đơn giản.",
    ),
    "ema": IndicatorSpec(
        name="ema",
        category="trend",
        compute=trend.compute_ema,
        input_kind="close",
        default_parameters={"period": 20},
        warmup=20,
        description="Đường trung bình động hàm mũ.",
    ),
    "adx": IndicatorSpec(
        name="adx",
        category="trend",
        compute=trend.compute_adx,
        input_kind="hlc",
        default_parameters={"period": 14},
        warmup=28,
        description="ADX kèm +DI/-DI đo sức mạnh và hướng xu hướng.",
    ),
    "ichimoku": IndicatorSpec(
        name="ichimoku",
        category="trend",
        compute=trend.compute_ichimoku,
        input_kind="hlc",
        default_parameters={"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52},
        warmup=78,
        description="Ichimoku Kinko Hyo và vị thế giá so với mây.",
    ),
    "rsi": IndicatorSpec(
        name="rsi",
        category="momentum",
        compute=momentum.compute_rsi,
        input_kind="close",
        default_parameters={"period": 14},
        warmup=15,
        description="Relative Strength Index.",
    ),
    "macd": IndicatorSpec(
        name="macd",
        category="momentum",
        compute=momentum.compute_macd,
        input_kind="close",
        default_parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
        warmup=35,
        description="MACD, đường tín hiệu và histogram.",
    ),
    "roc": IndicatorSpec(
        name="roc",
        category="momentum",
        compute=momentum.compute_roc,
        input_kind="close",
        default_parameters={"period": 12},
        warmup=13,
        description="Rate of Change theo phần trăm.",
    ),
    "volume_sma": IndicatorSpec(
        name="volume_sma",
        category="volume",
        compute=volume.compute_volume_sma,
        input_kind="volume",
        default_parameters={"period": 20},
        warmup=20,
        description="Trung bình khối lượng.",
    ),
    "rvol": IndicatorSpec(
        name="rvol",
        category="volume",
        compute=volume.compute_relative_volume,
        input_kind="volume",
        default_parameters={"period": 20},
        warmup=20,
        description="Khối lượng tương đối so với trung bình 20 phiên.",
    ),
    "obv": IndicatorSpec(
        name="obv",
        category="volume",
        compute=volume.compute_obv,
        input_kind="cv",
        default_parameters={},
        warmup=2,
        description="On Balance Volume.",
    ),
    "mfi": IndicatorSpec(
        name="mfi",
        category="volume",
        compute=volume.compute_mfi,
        input_kind="hlcv",
        default_parameters={"period": 14},
        warmup=15,
        description="Money Flow Index.",
    ),
    "bollinger": IndicatorSpec(
        name="bollinger",
        category="volatility",
        compute=volatility.compute_bollinger,
        input_kind="close",
        default_parameters={"period": 20, "stddev": 2.0},
        warmup=20,
        description="Bollinger Bands kèm %B.",
    ),
    "bb_width": IndicatorSpec(
        name="bb_width",
        category="volatility",
        compute=volatility.compute_bb_width,
        input_kind="close",
        default_parameters={"period": 20, "stddev": 2.0},
        warmup=20,
        description="Độ rộng dải Bollinger.",
    ),
    "atr": IndicatorSpec(
        name="atr",
        category="volatility",
        compute=volatility.compute_atr,
        input_kind="hlc",
        default_parameters={"period": 14},
        warmup=15,
        description="Average True Range.",
    ),
    "relative_strength": IndicatorSpec(
        name="relative_strength",
        category="relative",
        compute=relative_strength.compute_relative_strength,
        input_kind="benchmark",
        default_parameters={"period": 20},
        warmup=21,
        description="Hiệu suất cổ phiếu so với chỉ số tham chiếu.",
    ),
    "divergence": IndicatorSpec(
        name="divergence",
        category="momentum",
        compute=divergence.compute_divergence,
        input_kind="hlc",
        default_parameters={"rsi_period": 14, "pivot_length": 5, "max_gap_bars": 30},
        warmup=15,
        description="Phân kỳ thường và phân kỳ ẩn giữa giá và RSI.",
    ),
}


# Bộ chỉ báo tính mặc định cho một mã, theo đặc tả Phase 1.
DEFAULT_INDICATOR_REQUESTS: tuple[tuple[str, dict[str, int | float]], ...] = (
    ("sma", {"period": 10}),
    ("sma", {"period": 20}),
    ("sma", {"period": 50}),
    ("sma", {"period": 200}),
    ("ema", {"period": 10}),
    ("ema", {"period": 20}),
    ("ema", {"period": 50}),
    ("ema", {"period": 200}),
    ("adx", {"period": 14}),
    ("ichimoku", {"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52}),
    ("rsi", {"period": 14}),
    ("macd", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("roc", {"period": 12}),
    ("volume_sma", {"period": 20}),
    ("rvol", {"period": 20}),
    ("obv", {}),
    ("mfi", {"period": 14}),
    ("bollinger", {"period": 20, "stddev": 2.0}),
    ("bb_width", {"period": 20, "stddev": 2.0}),
    ("atr", {"period": 14}),
    ("relative_strength", {"period": 20}),
    ("divergence", {"rsi_period": 14, "pivot_length": 5, "max_gap_bars": 30}),
)


def get_spec(name: str) -> IndicatorSpec:
    """Lấy khai báo chỉ báo theo tên.

    Args:
        name: Tên chỉ báo.

    Returns:
        `IndicatorSpec` tương ứng.

    Raises:
        KeyError: Khi tên chỉ báo không có trong registry.
    """
    try:
        return INDICATORS[name]
    except KeyError as exc:
        raise KeyError(f"Chỉ báo không tồn tại: {name}") from exc


def list_indicators() -> list[dict[str, Any]]:
    """Liệt kê chỉ báo đã đăng ký để trả cho client.

    Returns:
        Danh sách dict gồm tên, nhóm, tham số mặc định và mô tả.
    """
    return [
        {
            "name": spec.name,
            "category": spec.category,
            "default_parameters": dict(spec.default_parameters),
            "warmup": spec.warmup,
            "description": spec.description,
        }
        for spec in INDICATORS.values()
    ]


def parameters_hash(parameters: dict[str, int | float | str]) -> str:
    """Tính hash canonical của tham số chỉ báo.

    Args:
        parameters: Tham số đã dùng.

    Returns:
        Chuỗi 16 ký tự hex, ổn định theo thứ tự khoá.
    """
    payload = json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _insufficient(spec: IndicatorSpec, parameters: dict[str, Any]) -> IndicatorOutput:
    """Tạo kết quả rỗng khi thiếu dữ liệu đầu vào bắt buộc."""
    return IndicatorOutput(
        indicator=spec.name,
        parameters=parameters,
        values={},
        warmup=spec.warmup,
    )


def compute_indicator(
    name: str,
    ohlcv: Ohlcv,
    parameters: dict[str, int | float] | None = None,
    *,
    benchmark: np.ndarray | None = None,
) -> IndicatorOutput:
    """Tính một chỉ báo theo tên và tham số.

    Args:
        name: Tên chỉ báo trong registry.
        ohlcv: Chuỗi OHLCV đầu vào.
        parameters: Tham số ghi đè tham số mặc định.
        benchmark: Chuỗi giá chỉ số tham chiếu, chỉ cần cho relative_strength.

    Returns:
        `IndicatorOutput` của chỉ báo.

    Raises:
        KeyError: Khi tên chỉ báo không tồn tại.
    """
    spec = get_spec(name)
    merged: dict[str, Any] = {**spec.default_parameters, **(parameters or {})}

    match spec.input_kind:
        case "close":
            return spec.compute(ohlcv.close, **merged)
        case "volume":
            return spec.compute(ohlcv.volume, **merged)
        case "cv":
            return spec.compute(ohlcv.close, ohlcv.volume, **merged)
        case "hlc":
            return spec.compute(ohlcv.high, ohlcv.low, ohlcv.close, **merged)
        case "hlcv":
            return spec.compute(ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume, **merged)
        case "benchmark":
            if benchmark is None or benchmark.size != ohlcv.close.size:
                return _insufficient(spec, merged)
            return spec.compute(ohlcv.close, benchmark, **merged)
        case _:  # pragma: no cover - phòng khi thêm input_kind mới mà quên xử lý
            raise ValueError(f"input_kind không hỗ trợ: {spec.input_kind}")


def compute_default_set(
    ohlcv: Ohlcv,
    *,
    benchmark: np.ndarray | None = None,
) -> list[IndicatorOutput]:
    """Tính bộ chỉ báo mặc định của Phase 1.

    Args:
        ohlcv: Chuỗi OHLCV đầu vào.
        benchmark: Chuỗi giá chỉ số tham chiếu cho relative_strength.

    Returns:
        Danh sách `IndicatorOutput` theo thứ tự khai báo trong
        `DEFAULT_INDICATOR_REQUESTS`.
    """
    return [
        compute_indicator(name, ohlcv, parameters, benchmark=benchmark)
        for name, parameters in DEFAULT_INDICATOR_REQUESTS
    ]
