"""Causal, one-to-one swing matching for regular MACD/RSI divergence.

Consumes CLOSED bars only. A price pivot at p is finalized at p + right + tolerance,
so the entire oscillator matching window is confirmed before emitting evidence.
"""

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal, Protocol

import numpy as np

from app.indicators.market_structure import find_pivot_highs, find_pivot_lows


@dataclass(frozen=True)
class DivergenceConfig:
    """Numeric configuration supports the existing indicator registry and cache hash."""

    rsi_period: int = 14
    pivot_length: int = 5
    max_gap_bars: int = 150
    pivot_right: int = 5
    tolerance_bars: int = 3
    min_gap_bars: int = 5
    lookback_bars: int = 500
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    use_histogram: int = 0
    enable_macd: int = 1
    enable_rsi: int = 1
    enable_bullish: int = 1
    enable_bearish: int = 1
    min_price_percent: float = 0
    min_macd_delta: float = 0
    min_rsi_delta: float = 0

    def __post_init__(self) -> None:
        """Reject nonfinite, fractional, oversized or contradictory parameters early."""
        for name, value in asdict(self).items():
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{name} must be finite numeric data")
            if name.startswith("min_") and name != "min_gap_bars":
                if value < 0:
                    raise ValueError(f"{name} must be nonnegative")
            elif int(value) != value:
                raise ValueError(f"{name} must be an integer")
        for name in (
            "use_histogram",
            "enable_macd",
            "enable_rsi",
            "enable_bullish",
            "enable_bearish",
        ):
            if getattr(self, name) not in (0, 1):
                raise ValueError(f"{name} must be 0 or 1")
        bounds = {
            "rsi_period": (2, 200),
            "pivot_length": (1, 50),
            "pivot_right": (1, 50),
            "max_gap_bars": (1, 2000),
            "tolerance_bars": (0, 20),
            "min_gap_bars": (1, 2000),
            "lookback_bars": (10, 2000),
            "macd_fast": (2, 200),
            "macd_slow": (3, 400),
            "macd_signal": (2, 200),
        }
        for name, (lower, upper) in bounds.items():
            if not lower <= getattr(self, name) <= upper:
                raise ValueError(f"{name} must be between {lower} and {upper}")
        if self.min_gap_bars > self.max_gap_bars or self.macd_fast >= self.macd_slow:
            raise ValueError("Require min_gap <= max_gap and MACD fast < slow")
        # Registry parsers may provide 5.0. Normalize to actual ints for slicing and TA-Lib.
        for name in bounds:
            object.__setattr__(self, name, int(getattr(self, name)))


def match_pivot(
    price_index: int, candidates: list[int], last_used: int, tolerance: int
) -> int | None:
    """Choose closest non-crossing unused oscillator extremum; older pivot wins ties."""
    start = bisect_left(candidates, max(price_index - tolerance, last_used + 1))
    stop = bisect_right(candidates, price_index + tolerance)
    return min(candidates[start:stop], key=lambda p: (abs(p - price_index), p), default=None)


class PivotDetector(Protocol):
    """Strategy boundary: return confirmed extrema, never unconfirmed future peaks."""

    def find(self, values: np.ndarray, left: int, right: int, high: bool) -> list[int]: ...


class ConfirmedPivotDetector:
    """Reuse the SMC pivot semantics on candle highs/lows and arbitrary line values."""

    def find(self, values: np.ndarray, left: int, right: int, high: bool) -> list[int]:
        return (find_pivot_highs if high else find_pivot_lows)(values, left, right)


@dataclass(frozen=True)
class IndicatorLine:
    """Adapter input for any aligned indicator line, independently of its formula."""

    name: str
    values: np.ndarray
    minimum_delta: float = 0


def unobstructed(values: np.ndarray, start: int, end: int, high: bool) -> bool:
    """Reject a connecting segment pierced by intermediate values (strict geometry)."""
    segment = values[start : end + 1]
    if end <= start or not np.isfinite(segment).all():
        return False
    chord = np.linspace(segment[0], segment[-1], len(segment))
    epsilon = max(1.0, float(np.max(np.abs(segment)))) * 1e-10
    return bool(np.all(segment <= chord + epsilon) if high else np.all(segment >= chord - epsilon))


def oscillator_context(name: str, values: np.ndarray, start: int, end: int) -> dict | None:
    """Describe oscillator extremes seen inside a divergence window.

    RSI is not required to be overbought/oversold at the confirmation bar. This
    context records whether the oscillator visited an extreme anywhere between the
    two matched pivots, so callers can verify that nuance without changing the
    divergence geometry itself.
    """
    if name != "RSI":
        return None
    window = np.asarray(values[start : end + 1], dtype=np.float64)
    finite = window[np.isfinite(window)]
    if finite.size == 0:
        return None
    low, high = float(finite.min()), float(finite.max())
    # Boolean flags are default-threshold hints; verification recomputes from raw
    # values using configurable RAG_RSI_OVERBOUGHT / RAG_RSI_OVERSOLD.
    return {
        "min": low,
        "max": high,
        "rsi_at_first_pivot": float(values[start]),
        "rsi_at_second_pivot": float(values[end]),
        "rsi_min_during_window": low,
        "rsi_max_during_window": high,
        "oversold_seen": low <= 30.0,
        "overbought_seen": high >= 70.0,
    }


class SwingDivergenceDetector:
    """Reusable Strategy-based detector; indicators plug in through IndicatorLine.

    Only consecutive price extrema AND consecutive oscillator extrema are compared.
    Missing matches never cause a search backward for an older convenient pair.
    These are explicit conservative engineering filters, not measured accuracy claims.
    """

    def __init__(self, pivots: PivotDetector | None = None):
        self.pivots = pivots or ConfirmedPivotDetector()

    def detect(
        self,
        high: np.ndarray,
        low: np.ndarray,
        lines: list[IndicatorLine],
        config: DivergenceConfig | None = None,
        *,
        closed_count: int | None = None,
    ) -> list[dict]:
        """Emit confirmed regular events using only the supplied closed prefix."""
        cfg = config or DivergenceConfig()
        arrays = [
            np.asarray(v, dtype=np.float64) for v in (high, low, *(line.values for line in lines))
        ]
        if any(v.ndim != 1 or v.size != arrays[0].size for v in arrays):
            raise ValueError("Price and indicator lines must be aligned 1-D arrays")
        if len({line.name for line in lines}) != len(lines):
            raise ValueError("Indicator line names must be unique")
        if any(
            not line.name or not isfinite(line.minimum_delta) or line.minimum_delta < 0
            for line in lines
        ):
            raise ValueError("Line names and thresholds must be valid")
        size = arrays[0].size if closed_count is None else closed_count
        if not isinstance(size, int) or not 0 <= size <= arrays[0].size:
            raise ValueError("closed_count must be a valid prefix")
        arrays = [np.where(np.isfinite(v[:size]), v[:size], np.nan) for v in arrays]
        events = []
        for is_high, prices, enabled in (
            (True, arrays[0], cfg.enable_bearish),
            (False, arrays[1], cfg.enable_bullish),
        ):
            if not enabled:
                continue
            price_pivots = self.pivots.find(prices, cfg.pivot_length, cfg.pivot_right, is_high)
            oscillator_pivots = [
                self.pivots.find(v, cfg.pivot_length, cfg.pivot_right, is_high) for v in arrays[2:]
            ]
            last_used = [-1] * len(lines)
            previous = None
            previous_matches = []
            for current in price_pivots:
                confirmed = current + cfg.pivot_right + cfg.tolerance_bars
                if confirmed >= size:
                    break
                matches = []
                for slot, candidates in enumerate(oscillator_pivots):
                    chosen = match_pivot(current, candidates, last_used[slot], cfg.tolerance_bars)
                    matches.append(chosen)
                    if chosen is not None:
                        last_used[slot] = chosen
                if previous is not None:
                    gap = current - previous
                    delta = float(prices[current] - prices[previous]) * (1 if is_high else -1)
                    threshold = abs(prices[previous]) * cfg.min_price_percent / 100
                    in_window = cfg.min_gap_bars <= gap <= min(cfg.max_gap_bars, cfg.lookback_bars)
                    # Regular: price tạo đỉnh/đáy mới còn oscillator thì không.
                    # Hidden: giá tạo đỉnh thấp hơn / đáy cao hơn nhưng oscillator
                    # lại tạo đỉnh/đáy mới - dấu hiệu xu hướng tiếp diễn.
                    setups = (
                        ("regular", delta > 0 and delta >= threshold, False),
                        ("hidden", delta < 0 and -delta >= threshold, True),
                    )
                    for kind, price_ok, oscillator_positive in setups:
                        if not (in_window and price_ok):
                            continue
                        if not unobstructed(prices, previous, current, is_high):
                            continue
                        matched_lines = []
                        for slot, line in enumerate(lines):
                            start, end = previous_matches[slot], matches[slot]
                            if start is None or end is None:
                                continue
                            candidates = oscillator_pivots[slot]
                            if bisect_left(candidates, end) != bisect_left(candidates, start) + 1:
                                continue
                            values = arrays[slot + 2]
                            change = float(values[end] - values[start]) * (1 if is_high else -1)
                            if oscillator_positive:
                                if change <= 0 or change < line.minimum_delta:
                                    continue
                            elif (
                                change >= 0
                                or -change < line.minimum_delta
                                or not unobstructed(values, start, end, is_high)
                            ):
                                continue
                            match_payload = dict(
                                indicator=line.name,
                                start_index=start,
                                end_index=end,
                                start_value=float(values[start]),
                                end_value=float(values[end]),
                            )
                            context = oscillator_context(line.name, values, start, end)
                            if context is not None:
                                match_payload["rsi_context"] = context
                            matched_lines.append(match_payload)
                        if matched_lines:
                            events.append(
                                dict(
                                    kind=kind,
                                    direction="bearish" if is_high else "bullish",
                                    start_index=previous,
                                    end_index=current,
                                    start_price=float(prices[previous]),
                                    end_price=float(prices[current]),
                                    price_confirmed_index=current + cfg.pivot_right,
                                    confirmed_index=confirmed,
                                    oscillators=matched_lines,
                                )
                            )
                previous, previous_matches = current, matches
        return sorted(
            events, key=lambda e: (e["confirmed_index"], e["direction"], e["start_index"])
        )


def detect_divergence(
    high: np.ndarray,
    low: np.ndarray,
    macd: np.ndarray,
    rsi: np.ndarray,
    config: DivergenceConfig | None = None,
    *,
    closed_count: int | None = None,
) -> list[dict]:
    """Compatibility adapter: wrap MACD/RSI as generic indicator lines."""
    cfg = config or DivergenceConfig()
    lines = []
    if cfg.enable_macd:
        lines.append(IndicatorLine("MACD", macd, cfg.min_macd_delta))
    if cfg.enable_rsi:
        lines.append(IndicatorLine("RSI", rsi, cfg.min_rsi_delta))
    return SwingDivergenceDetector().detect(high, low, lines, cfg, closed_count=closed_count)


def event_alerts(event: dict) -> list[str]:
    """Six logical alert kinds; a combined event also carries independent alerts."""
    direction: Literal["bullish", "bearish"] = event["direction"]
    names = [o["indicator"].lower() for o in event["oscillators"]]
    result = [f"{name}_{direction}_divergence" for name in names]
    if set(names) == {"macd", "rsi"}:
        result.append(f"macd_rsi_{direction}_divergence")
    return result
