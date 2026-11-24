"""Reusable decorator for any synchronous, aligned numeric indicator line."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

import numpy as np

from app.indicators import Ohlcv
from app.indicators.swing_divergence import DivergenceConfig, IndicatorLine, SwingDivergenceDetector


@dataclass(frozen=True)
class DivergenceLineResult:
    """Keep the original line alongside confirmed divergence evidence."""

    name: str
    values: np.ndarray
    events: list[dict]


def phan_ky(
    indicator: Callable[..., np.ndarray] | None = None,
    *,
    name: str | None = None,
    minimum_delta: float = 0,
    detector: SwingDivergenceDetector | None = None,
):
    """Wrap ABC(ohlcv, **params) with @phan_ky or phan_ky(ABC).

    The function must return ONE 1-D numeric array aligned to OHLCV. Select a line
    explicitly from multi-output indicators (e.g. MACD tuple). Reserved call kwargs
    divergence_config and closed_count belong to this wrapper, not the indicator.
    Enforces closed-prefix evaluation before ABC runs, preventing forming-tail use.
    """

    def decorate(function: Callable[..., np.ndarray]):
        engine = detector or SwingDivergenceDetector()
        line_name = name or function.__name__.upper()

        @wraps(function)
        def wrapped(
            ohlcv: Ohlcv,
            *args: Any,
            divergence_config: DivergenceConfig | None = None,
            closed_count: int | None = None,
            **kwargs: Any,
        ) -> DivergenceLineResult:
            count = len(ohlcv) if closed_count is None else closed_count
            if not isinstance(count, int) or not 0 <= count <= len(ohlcv):
                raise ValueError("closed_count must be a valid prefix")
            if any(
                np.asarray(v).ndim != 1 or len(v) != len(ohlcv)
                for v in (ohlcv.open, ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume)
            ):
                raise ValueError("OHLCV arrays must be aligned")
            prefix = Ohlcv(
                *(v[:count] for v in (ohlcv.open, ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume))
            )
            values = np.asarray(function(prefix, *args, **kwargs), dtype=np.float64)
            events = engine.detect(
                prefix.high,
                prefix.low,
                [IndicatorLine(line_name, values, minimum_delta)],
                divergence_config,
            )
            return DivergenceLineResult(line_name, values, events)

        return wrapped

    return decorate(indicator) if indicator is not None else decorate
