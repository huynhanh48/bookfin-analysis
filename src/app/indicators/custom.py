"""Custom/composite adapters retain project-specific formula and signal semantics."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from app.indicators import IndicatorOutput
from app.indicators.engine import CustomIndicator, IndicatorBatchResult, IndicatorFrame


class FunctionIndicator(CustomIndicator):
    """Adapt legacy calculation + interpretation without deleting trusted old code.

    Historical values are evaluated on prefixes, preserving causal custom behavior.
    Standard TA-Lib adapters use full-series native calculation instead.
    """

    def __init__(self, name: str, parameters: dict, calculate: Callable[[Any], IndicatorOutput]):
        self.name, self.parameters, self._calculate = name, parameters, calculate

    def latest(self, ohlcv) -> IndicatorOutput:
        """Adapt the existing latest-value API without recomputing historical prefixes."""
        return self._calculate(ohlcv)

    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        """Return project-composite series and original event evidence."""
        self.validate(frame)
        from app.indicators import Ohlcv

        points = []
        for i in range(1, len(frame.ohlcv) + 1):
            prefix = Ohlcv(
                *(getattr(frame.ohlcv, k)[:i] for k in ("open", "high", "low", "close", "volume"))
            )
            points.append(self._calculate(prefix))
        keys = list(points[-1].values) if points else []
        values = {
            key: np.array(
                [
                    point.values.get(key) if point.values.get(key) is not None else np.nan
                    for point in points
                ],
                dtype=np.float64,
            )
            for key in keys
        }
        return IndicatorBatchResult(
            self.name,
            dict(self.parameters),
            values,
            frame.timeframe,
            frame.timestamps,
            max(0, points[-1].warmup - 1) if points else 0,
            events=points[-1].events if points else [],
        )


class MACDDivergenceIndicator(FunctionIndicator):
    """Confirmed custom price/MACD swings built on TA-Lib MACD."""


class RSIDivergenceIndicator(FunctionIndicator):
    """Confirmed custom price/RSI swings built on TA-Lib RSI."""


class MarketStructureIndicator(CustomIndicator):
    """Adapter around existing confirmed swing/BOS/CHoCH/MSS engine and ATR primitive."""

    name = "market_structure"

    def __init__(self, config=None):
        from app.indicators.market_structure import MarketStructureConfig

        self.config = config or MarketStructureConfig()
        self.parameters = self.config.to_dict()

    def analyze(self, *args, **kwargs):
        """Preserve the established SMC snapshot API and state machine."""
        from app.indicators.market_structure import MarketStructureEngine

        return MarketStructureEngine(self.config).analyze(*args, **kwargs)

    def snapshot(self, frame: IndicatorFrame, symbol: str = ""):
        """Return the complete confirmed swing/structure snapshot for evidence consumers."""
        self.validate(frame)
        from app.indicators.market_structure import CandleBar

        bars = [
            CandleBar(
                t,
                *(
                    float(getattr(frame.ohlcv, k)[i])
                    for k in ("open", "high", "low", "close", "volume")
                ),
            )
            for i, t in enumerate(frame.timestamps)
        ]
        return self.analyze(bars, symbol=symbol, timeframe=frame.timeframe)

    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        """Expose SMC events through the common calculation contract."""
        snapshot = self.snapshot(frame)
        return IndicatorBatchResult(
            self.name,
            dict(self.parameters),
            {},
            frame.timeframe,
            frame.timestamps,
            self.config.left + self.config.right,
            events=[asdict(e) for e in snapshot.events],
        )


class BreakoutIndicator(MarketStructureIndicator):
    """Composite reusing configured ATR, wick/close breaks and false-breakout rules."""

    name = "breakout"
