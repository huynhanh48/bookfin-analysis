"""Typed divergence evidence and bounded query configuration for the existing API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.indicators.swing_divergence import DivergenceConfig
from app.schemas.market import Timeframe


class RsiContextOut(BaseModel):
    """RSI extremes and pivot values inside the matched divergence window."""

    min: float
    max: float
    rsi_at_first_pivot: float | None = None
    rsi_at_second_pivot: float | None = None
    rsi_min_during_window: float | None = None
    rsi_max_during_window: float | None = None
    oversold_seen: bool = False
    overbought_seen: bool = False


class OscillatorMatchOut(BaseModel):
    """Two independently confirmed oscillator extrema matched to a price pair."""

    indicator: Literal["MACD", "RSI"]
    start_index: int
    end_index: int
    start_time: datetime
    end_time: datetime
    start_value: float
    end_value: float
    rsi_context: RsiContextOut | None = None


class DivergenceEventOut(BaseModel):
    """Stable event identity and historical geometry, separate from confirmation time.

    All timestamps identify candle OPEN times, matching existing chart/structure
    conventions. confirmed_at denotes the bar whose CLOSE makes the alert valid.
    """

    id: str
    kind: Literal["regular"]
    direction: Literal["bullish", "bearish"]
    start_index: int
    end_index: int
    start_time: datetime
    end_time: datetime
    start_price: float
    end_price: float
    price_confirmed_at: datetime
    confirmed_at: datetime
    label: str
    alerts: list[str]
    oscillators: list[OscillatorMatchOut]


class DivergenceQuery(BaseModel):
    """Named API parameters; no arbitrary unlimited scans or negative thresholds."""

    timeframe: Timeframe = "1D"
    limit: int = Field(default=500, ge=20, le=2000)
    rsi_period: int = Field(default=14, ge=2, le=200)
    pivot_length: int = Field(default=5, ge=1, le=50)
    max_gap_bars: int = Field(default=150, ge=1, le=2000)
    pivot_right: int = Field(default=5, ge=1, le=50)
    tolerance_bars: int = Field(default=3, ge=0, le=20)
    min_gap_bars: int = Field(default=5, ge=1, le=2000)
    lookback_bars: int = Field(default=500, ge=10, le=2000)
    macd_fast: int = Field(default=12, ge=2, le=200)
    macd_slow: int = Field(default=26, ge=3, le=400)
    macd_signal: int = Field(default=9, ge=2, le=200)
    use_histogram: int = Field(default=0, ge=0, le=1)
    enable_macd: int = Field(default=1, ge=0, le=1)
    enable_rsi: int = Field(default=1, ge=0, le=1)
    enable_bullish: int = Field(default=1, ge=0, le=1)
    enable_bearish: int = Field(default=1, ge=0, le=1)
    min_price_percent: float = Field(default=0, ge=0, allow_inf_nan=False)
    min_macd_delta: float = Field(default=0, ge=0, allow_inf_nan=False)
    min_rsi_delta: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_configuration(self):
        """Apply exactly the same cross-field constraints as the production engine."""
        DivergenceConfig(**self.model_dump(exclude={"timeframe", "limit"}))
        return self
