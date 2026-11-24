"""Audited native call boundary shared by standard wrappers and custom primitives."""

from typing import Any

import numpy as np
import talib
from talib import abstract

# Query installed public metadata; deprecated no-op settings are not advertised.
UNSTABLE_FUNCTIONS = frozenset(
    name
    for name in talib.get_functions()
    if "Function has an unstable period" in abstract.Function(name).info["function_flags"]  # type: ignore[attr-defined]
)


def native_series(name: str, *inputs: np.ndarray, **parameters: Any):
    """Dispatch only verified functions, normalize float64 and preserve output NaNs."""
    if name not in talib.get_functions():
        raise ValueError(f"Unavailable TA-Lib function: {name}")
    function = abstract.Function(name)  # type: ignore[attr-defined]  # upstream stub omits factory
    if set(parameters) != set(function.parameters):
        raise ValueError(f"Explicit parameters required for {name}: {list(function.parameters)}")
    arrays = [np.ascontiguousarray(v, dtype=np.float64) for v in inputs]
    if any(a.ndim != 1 or len(a) != len(arrays[0]) for a in arrays):
        raise ValueError("Native inputs must be aligned 1-D arrays")
    from app.indicators.engine import TA_LOCK

    with TA_LOCK:
        return getattr(talib, name)(*arrays, **parameters)


def required_bars(name: str, **parameters: Any) -> int:
    """Lookback is an index; minimum required observations are lookback + 1."""
    function = abstract.Function(name)  # type: ignore[attr-defined]  # upstream stub omits factory
    function.set_parameters(parameters)
    return int(function.lookback) + 1


def unstable_metadata() -> dict[str, int]:
    """Capture nonzero process-wide unstable settings for result lineage/cache identity."""
    from app.indicators.engine import TA_LOCK

    with TA_LOCK:
        values = {}
        for name in sorted(UNSTABLE_FUNCTIONS):
            try:
                period = talib.get_unstable_period(name)
            except KeyError:
                continue  # Public API rejects functions without an unstable period.
            if period:
                values[name] = period
        return values


def configure_unstable_periods(periods: dict[str, int]) -> None:
    """Configure global unstable periods once at process startup, not per request."""
    from app.indicators.engine import TA_LOCK

    with TA_LOCK:
        for name, period in periods.items():
            if name not in UNSTABLE_FUNCTIONS or not isinstance(period, int) or period < 0:
                raise ValueError("Invalid unstable-period configuration")
            try:
                talib.get_unstable_period(name)
            except KeyError as exc:
                raise ValueError(f"{name} does not support unstable periods") from exc
        for name, period in periods.items():
            talib.set_unstable_period(name, period)
