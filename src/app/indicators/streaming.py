"""Closed-bar streaming wrapper for the verified TA-Lib 0.8.3 handle API."""

from datetime import datetime
from typing import Any

import numpy as np
from talib import stream

from app.indicators.engine import TA_LOCK, IndicatorFrame, TalibIndicator


class ConfirmedStream:
    """Own a nonserializable native handle; rebuild from PostgreSQL after restart.

    Duplicate final bars are idempotent; corrections/out-of-order require replay.
    Forming bars use peek and never change confirmed state or persistence.
    """

    def __init__(self, indicator: TalibIndicator, history: IndicatorFrame):
        if indicator.unstable:
            raise ValueError("Configure process-wide unstable periods before opening a stream")
        from app.indicators.native import unstable_metadata

        self.unstable_periods = unstable_metadata()
        selected = history.prefix()
        indicator.validate(selected)
        if not selected.timestamps:
            raise ValueError("Streaming initialization requires closed history")
        self.indicator = indicator
        self.last_time = selected.timestamps[-1]
        self.last_inputs = tuple(
            float(getattr(selected.ohlcv, k)[-1]) for k in indicator.required_inputs
        )
        with TA_LOCK:
            self.handle = getattr(stream, indicator.function)(
                *(getattr(selected.ohlcv, k) for k in indicator.required_inputs),
                **indicator.parameters,
            )

    def update(self, timestamp: datetime, bar: dict[str, float], *, final: bool = True) -> Any:
        """Advance once per closed candle or return a nonmutating provisional peek."""
        from app.indicators.native import unstable_metadata

        if unstable_metadata() != self.unstable_periods:
            raise ValueError("Unstable settings changed; stream requires replay")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Aware timestamp required")
        values = tuple(float(bar[k]) for k in self.indicator.required_inputs)
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite stream input")
        if timestamp < self.last_time:
            raise ValueError("Out-of-order stream bar requires replay")
        if timestamp == self.last_time:
            if values != self.last_inputs:
                raise ValueError("Historical correction requires replay")
            return self.handle.value
        with TA_LOCK:
            result = self.handle.update(*values) if final else self.handle.peek(*values)
        if final:
            self.last_time, self.last_inputs = timestamp, values
        return result
