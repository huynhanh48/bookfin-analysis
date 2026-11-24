"""Project-owned indicator contracts and TA-Lib adapters; independent of HTTP/DB."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any, Protocol

import numpy as np
import talib
from talib import abstract

from app.indicators import Ohlcv

FORMULA_VERSION = "talib-foundation-1"
TA_LOCK = RLock()


@dataclass(frozen=True)
class IndicatorFrame:
    """Aligned float64 OHLCV, aware ascending timestamps and explicit finality prefix."""

    ohlcv: Ohlcv
    timestamps: Sequence[datetime]
    timeframe: str = "1D"
    closed_count: int | None = None

    def validate(self) -> None:
        """Reject duplicates, invalid bars, nonfinite volume and missing input data.

        Missing prices are rejected at the ingestion boundary, never filled with zero.
        TA-Lib output warmup NaNs remain untouched. A provisional tail is permitted.
        """
        arrays = [
            np.asarray(getattr(self.ohlcv, k)) for k in ("open", "high", "low", "close", "volume")
        ]
        if arrays[3].ndim != 1:
            raise ValueError("Close must be a 1-D array")
        n = len(arrays[3])
        if any(a.ndim != 1 or len(a) != n or not np.isfinite(a).all() for a in arrays):
            raise ValueError(
                "OHLCV must be aligned finite 1-D arrays; missing bars need source repair"
            )
        if len(self.timestamps) != n or any(
            t.tzinfo is None or t.utcoffset() is None for t in self.timestamps
        ):
            raise ValueError("Require an aware timestamp for every bar")
        if any(b <= a for a, b in zip(self.timestamps, self.timestamps[1:], strict=False)):
            raise ValueError("Timestamps must be strictly increasing without duplicates")
        opening, high, low, close, volume = arrays
        if (
            np.any(high < np.maximum.reduce([opening, low, close]))
            or np.any(low > np.minimum.reduce([opening, high, close]))
            or np.any(volume < 0)
        ):
            raise ValueError("Invalid OHLC range or negative volume")
        if self.closed_count is not None and (
            not isinstance(self.closed_count, int) or not 0 <= self.closed_count <= n
        ):
            raise ValueError("closed_count must identify a valid closed prefix")

    def prefix(self, provisional: bool = False) -> "IndicatorFrame":
        """Separate confirmed calculation from a nonpersistent provisional view."""
        self.validate()
        count = len(self.ohlcv) if provisional or self.closed_count is None else self.closed_count
        data = Ohlcv(
            *(
                np.ascontiguousarray(getattr(self.ohlcv, k)[:count], dtype=np.float64)
                for k in ("open", "high", "low", "close", "volume")
            )
        )
        return IndicatorFrame(data, self.timestamps[:count], self.timeframe, count)


@dataclass(frozen=True)
class IndicatorBatchResult:
    """Full aligned series with formula/runtime versions and explicit warmup metadata."""

    name: str
    params: dict[str, Any]
    series: dict[str, np.ndarray]
    timeframe: str
    timestamps: Sequence[datetime]
    lookback: int
    provisional: bool = False
    formula_version: str = FORMULA_VERSION
    ta_lib_version: str = talib.__version__
    c_library_version: str = talib.__ta_version__.decode()
    events: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Require all output lines to be ready at the latest timestamp."""
        return (
            "ok"
            if len(self.timestamps) > self.lookback
            and all(len(v) and np.isfinite(v[-1]) for v in self.series.values())
            else "insufficient_history"
        )


class BaseIndicator(Protocol):
    """Business-facing contract: callers do not depend on talib.abstract.Function."""

    name: str
    required_inputs: tuple[str, ...]
    parameters: Mapping[str, Any]

    def validate(self, frame: IndicatorFrame) -> None: ...
    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult: ...


class CustomIndicator(ABC):
    """Base for project composites using native primitives or confirmed structure rules."""

    name: str
    required_inputs: tuple[str, ...] = ("high", "low", "close")
    parameters: Mapping[str, Any]

    def validate(self, frame: IndicatorFrame) -> None:
        """Share boundary validation with standard native indicators."""
        frame.validate()

    @abstractmethod
    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        """Return aligned series without writing persistence or interpreting trades."""


class TalibIndicator:
    """Native Function API execution, Abstract API metadata and isolated unstable settings.

    A fresh Function per call prevents shared mutable parameter/input state. Global
    unstable configuration is protected by one lock and restored after calculation.
    """

    def __init__(
        self,
        name: str,
        function: str,
        parameters: Mapping[str, Any],
        outputs: Sequence[str] | None = None,
        inputs: Sequence[str] | None = None,
        unstable: Mapping[str, int] | None = None,
    ):
        self.name = name
        self.function = function.upper()
        if self.function not in talib.get_functions():
            raise ValueError(f"Unsupported installed TA-Lib function: {function}")
        metadata = abstract.Function(self.function)  # type: ignore[attr-defined]  # upstream stub omits factory
        expected = dict(metadata.parameters)
        if set(parameters) != set(expected):
            raise ValueError(f"Explicit parameters required: {list(expected)}")
        self.parameters = dict(parameters)
        self.outputs = tuple(outputs or metadata.output_names)
        self.required_inputs = tuple(
            inputs
            or [
                value
                for values in metadata.input_names.values()
                for value in ([values] if isinstance(values, str) else values)
            ]
        )
        self.unstable = dict(unstable or {})
        from app.indicators.native import UNSTABLE_FUNCTIONS

        if not self.unstable.keys() <= UNSTABLE_FUNCTIONS:
            raise ValueError("Function does not support an unstable period")
        if len(self.outputs) != len(metadata.output_names) or any(
            not isinstance(v, int) or v < 0 for v in self.unstable.values()
        ):
            raise ValueError("Invalid output mapping or unstable period")
        try:
            metadata.set_parameters(self.parameters)
            if metadata.lookback < 0 or any(not np.isfinite(v) for v in self.parameters.values()):
                raise ValueError("Invalid native parameter range")
        except (TypeError, OverflowError) as exc:
            raise ValueError(f"Invalid parameters for {self.function}") from exc

    def validate(self, frame: IndicatorFrame) -> None:
        """Validate market data and mapped native inputs before invoking C code."""
        frame.validate()
        if any(
            name not in ("open", "high", "low", "close", "volume") for name in self.required_inputs
        ):
            raise ValueError("Invalid TA-Lib input mapping")

    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        """Calculate one native series using explicit parameters and float64 inputs."""
        self.validate(frame)
        arrays = [
            np.ascontiguousarray(getattr(frame.ohlcv, k), dtype=np.float64)
            for k in self.required_inputs
        ]
        with TA_LOCK:
            old = {key: talib.get_unstable_period(key) for key in self.unstable}
            try:
                for key, value in self.unstable.items():
                    talib.set_unstable_period(key, value)
                metadata = abstract.Function(self.function)  # type: ignore[attr-defined]  # upstream stub omits factory
                metadata.set_parameters(self.parameters)
                lookback = metadata.lookback
                raw = getattr(talib, self.function)(*arrays, **self.parameters)
            finally:
                for key, value in old.items():
                    talib.set_unstable_period(key, value)
        values = raw if isinstance(raw, tuple) else (raw,)
        return IndicatorBatchResult(
            self.name,
            dict(self.parameters),
            dict(zip(self.outputs, values, strict=True)),
            frame.timeframe,
            frame.timestamps,
            lookback,
        )


class IndicatorRegistry:
    """Factory registry with explicit duplicate rejection and no global magic discovery."""

    def __init__(self):
        self._factories: dict[str, Callable[..., BaseIndicator]] = {}

    def register(self, name: str, factory: Callable[..., BaseIndicator]) -> None:
        """Register a configured adapter factory; duplicate names are errors."""
        if name in self._factories:
            raise ValueError(f"Duplicate indicator: {name}")
        self._factories[name] = factory

    def get(self, name: str, **parameters: Any) -> BaseIndicator:
        """Construct an indicator without importing provider code in business callers."""
        return self._factories[name](**parameters)


class IndicatorEngine:
    """Validate once, select closed/provisional prefix and orchestrate registry adapters."""

    def __init__(self, registry: IndicatorRegistry):
        self.registry = registry

    def calculate(
        self,
        frame: IndicatorFrame,
        requests: Sequence[tuple[str, dict]],
        *,
        provisional: bool = False,
    ) -> list[IndicatorBatchResult]:
        """Return calculation data only; provisional results must not be persisted."""
        from dataclasses import replace

        selected = frame.prefix(provisional)
        return [
            replace(self.registry.get(name, **params).calculate(selected), provisional=provisional)
            for name, params in requests
        ]
