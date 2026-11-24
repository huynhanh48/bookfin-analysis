"""Đăng ký chỉ báo: ánh xạ tên chỉ báo sang hàm tính và tham số mặc định.

Registry là điểm mở rộng duy nhất khi thêm chỉ báo mới: khai báo một
`IndicatorSpec`, phần còn lại của hệ thống (service, worker, API) tự dùng được.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from functools import partial
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
from app.indicators.factory import NATIVE_SPECS
from app.indicators.swing_divergence import DivergenceConfig

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
    "rvol_v2": IndicatorSpec(
        name="rvol_v2",
        category="volume",
        compute=volume.compute_relative_volume_previous,
        input_kind="volume",
        default_parameters={"period": 20},
        warmup=21,
        description="RVOL v2 so với trung bình 20 phiên trước, không gồm nến hiện tại.",
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
        default_parameters=asdict(DivergenceConfig()),
        warmup=15,
        description="Regular divergence từ swing giá và swing MACD/RSI độc lập.",
    ),
}


def _native_spec_compute(name, *arrays, **parameters):
    """Native registration callback for direct consumers of IndicatorSpec.compute."""
    from app.indicators import last_value
    from app.indicators.native import native_series, required_bars

    fn, mapping, outputs = NATIVE_SPECS[name]
    params = {target: parameters[key] for key, target in mapping.items()}
    raw = native_series(fn, *arrays, **params)
    values = raw if isinstance(raw, tuple) else (raw,)
    return IndicatorOutput(
        name,
        parameters,
        dict(zip(outputs, [last_value(v) for v in values], strict=True)),
        required_bars(fn, **params),
    )


for _name, _defaults, _kind in [
    ("wma", {"period": 20}, "close"),
    ("ma", {"period": 20, "matype": 0}, "close"),
    ("plus_di", {"period": 14}, "hlc"),
    ("minus_di", {"period": 14}, "hlc"),
    ("ad", {}, "hlcv"),
    ("adosc", {"fast_period": 3, "slow_period": 10}, "hlcv"),
    ("rvol_native", {"period": 20}, "volume"),
    ("supertrend", {"period": 10, "multiplier": 3.0}, "hlc"),
]:
    INDICATORS[_name] = IndicatorSpec(
        _name,
        "native",
        partial(_native_spec_compute, _name),
        _kind,
        _defaults,
        0,
        f"Native TA-Lib {NATIVE_SPECS[_name][0]}; explicit parameters.",
    )


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
    ("divergence", {}),
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


def _default_warmup(spec: IndicatorSpec) -> int:
    """Expose installed lookback metadata, retaining custom formula warmups."""
    from app.indicators.native import required_bars

    if spec.name in NATIVE_SPECS:
        fn, mapping, _ = NATIVE_SPECS[spec.name]
        return required_bars(
            fn, **{target: spec.default_parameters[key] for key, target in mapping.items()}
        )
    return spec.warmup


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
            "warmup": _default_warmup(spec),
            "description": spec.description,
        }
        for spec in INDICATORS.values()
    ]


def parse_request(text: str) -> tuple[str, dict[str, int | float]]:
    """Phân tích cú pháp `name` hoặc `name:v1:v2` thành tên chỉ báo và tham số.

    Tham số được gán theo đúng thứ tự khai báo trong `default_parameters` của
    chỉ báo, ví dụ `macd:12:26:9` là fast, slow và signal period.

    Args:
        text: Chuỗi do client gửi, ví dụ `sma:20`.

    Returns:
        Cặp (tên chỉ báo, tham số ghi đè).

    Raises:
        ValueError: Khi tên chỉ báo không tồn tại, sai số tham số hoặc tham số
            không phải số.
    """
    parts = [part.strip() for part in text.split(":")]
    name = parts[0].lower()
    spec = get_spec(name)
    raw_parameters = [part for part in parts[1:] if part]
    if len(raw_parameters) > len(spec.default_parameters):
        raise ValueError(
            f"Chỉ báo {name} nhận tối đa {len(spec.default_parameters)} tham số, "
            f"nhận được {len(raw_parameters)}."
        )
    parameters: dict[str, int | float] = {}
    for key, raw in zip(spec.default_parameters, raw_parameters, strict=False):
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"Tham số {key} của {name} không phải số: {raw}") from exc
        parameters[key] = int(value) if value.is_integer() else value
    if name == "divergence":
        DivergenceConfig(**{**spec.default_parameters, **parameters})
    elif name in NATIVE_SPECS:
        from app.indicators.factory import build_indicator

        build_indicator(name, {**spec.default_parameters, **parameters})
    return name, parameters


def parameters_hash(parameters: Mapping[str, Any]) -> str:
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


def _legacy_compute_indicator(
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


def compute_indicator(
    name: str,
    ohlcv: Ohlcv,
    parameters: dict[str, int | float] | None = None,
    *,
    benchmark: np.ndarray | None = None,
) -> IndicatorOutput:
    """Compatibility facade: project adapter calculates, old signal interpretation remains."""
    from datetime import UTC, datetime, timedelta

    from app.indicators.engine import IndicatorFrame
    from app.indicators.factory import build_indicator

    spec = get_spec(name)
    merged = {**spec.default_parameters, **(parameters or {})}
    # Custom rules retain their causal event calculation; native/composites have full series.

    if name not in NATIVE_SPECS and name not in ("adx", "bollinger", "bb_width", "rvol"):
        return build_indicator(name, merged, benchmark).latest(ohlcv)
    adapter = build_indicator(name, merged, benchmark)
    # Array-only legacy entrypoint has no timestamp argument; synthetic positions are
    # internal only. Service uses actual timestamps in IndicatorFrame for validation.
    frame = IndicatorFrame(
        ohlcv, [datetime(2000, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(ohlcv))]
    )
    result = adapter.calculate(frame)
    from app.indicators import last_value
    from app.indicators.factory import interpret_signal

    values = {k: last_value(v) for k, v in result.series.items()}
    previous = {k: last_value(v[:-1]) for k, v in result.series.items()}
    signal = interpret_signal(
        name, values, previous, float(ohlcv.close[-1]) if len(ohlcv) else np.nan
    )
    return IndicatorOutput(
        name,
        dict(merged),
        {k: last_value(v) for k, v in result.series.items()},
        result.lookback + 1,
        signal,
    )
