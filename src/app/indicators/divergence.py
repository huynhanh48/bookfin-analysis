"""Application divergence indicator backed by causal price/oscillator swing matching."""

from dataclasses import asdict

import numpy as np
import talib  # noqa: F401 -- compatibility for existing instrumentation

from app.indicators import IndicatorOutput, as_float_array, last_value
from app.indicators.native import native_series
from app.indicators.swing_divergence import DivergenceConfig, detect_divergence

# Preserve existing clients' four aggregate keys; Hidden is intentionally disabled.
DIVERGENCE_KEYS = ("bearish_regular", "bullish_regular", "bearish_hidden", "bullish_hidden")
# Tăng khi thuật toán phát hiện thay đổi; snapshot cũ sẽ tự tính lại.
ALGORITHM_VERSION = "swing-div-4"


def divergence_history(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, config: DivergenceConfig
) -> list[dict]:
    """Calculate RSI/MACD on Close once and detect all confirmed regular events."""
    close = as_float_array(close)
    rsi = native_series("RSI", close, timeperiod=config.rsi_period)
    macd, _, histogram = native_series(
        "MACD",
        close,
        fastperiod=config.macd_fast,
        slowperiod=config.macd_slow,
        signalperiod=config.macd_signal,
    )
    return detect_divergence(high, low, histogram if config.use_histogram else macd, rsi, config)


def compute_divergence(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = 14,
    pivot_length: int = 5,
    max_gap_bars: int = 150,
    **options: int | float,
) -> IndicatorOutput:
    """Return confirmation-bar pulses and full traceable historical event evidence.

    First three parameters retain the original registry request format. Hidden
    aggregate flags remain zero; historical events do not repeat on later candles.
    """
    cfg = DivergenceConfig(
        rsi_period=rsi_period, pivot_length=pivot_length, max_gap_bars=max_gap_bars, **options
    )
    warmup = cfg.pivot_length + cfg.pivot_right + cfg.tolerance_bars + 1
    values = dict.fromkeys(DIVERGENCE_KEYS, 0.0 if len(close) >= warmup else None)
    events = divergence_history(high, low, close, cfg) if len(close) >= warmup else []
    active = [event for event in events if event["confirmed_index"] == len(close) - 1]
    for event in active:
        values[f"{event['direction']}_{event['kind']}"] = 1.0
    return IndicatorOutput(
        indicator="divergence",
        parameters={**asdict(cfg), "algorithm_version": ALGORITHM_VERSION},
        values=values,
        warmup=warmup,
        signal=f"{active[0]['direction']}_{active[0]['kind']}" if active else None,
        events=events,
    )


def latest_rsi(close: np.ndarray, *, period: int = 14) -> float | None:
    """Return the latest finite RSI for callers using the original debug helper."""
    return last_value(native_series("RSI", as_float_array(close), timeperiod=period))
