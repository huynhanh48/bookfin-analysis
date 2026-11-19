"""Small offline specification oracle for Pine matching; not a Pine interpreter.

Inputs are price and precomputed oscillator series. Tests verify closed-bar timing,
matching and numeric divergence, not TradingView's compiler or TA calculations.
"""

from dataclasses import dataclass
from math import isfinite


@dataclass
class Pivot:
    """A local extremum with formation and confirmation bars."""

    bar: int
    value: float
    confirmed: int
    used: bool = False


def pivots(values: list[float], left: int, right: int, high: bool) -> list[Pivot]:
    """Unique extrema for deterministic fixtures; plateau parity is not assumed."""
    result = []
    for bar in range(left, len(values) - right):
        window = values[bar - left : bar + right + 1]
        if not all(isfinite(value) for value in window):
            continue
        value = values[bar]
        if value == (max(window) if high else min(window)) and window.count(value) == 1:
            result.append(Pivot(bar, value, bar + right))
    return result


def match(price: Pivot, candidates: list[Pivot], tolerance: int, last: int) -> Pivot | None:
    """Nearest available confirmed pivot, older tie, chronological non-crossing use."""
    available = [
        p
        for p in candidates
        if not p.used
        and p.bar > last
        and abs(p.bar - price.bar) <= tolerance
        and p.confirmed <= price.confirmed + tolerance
    ]
    chosen = min(available, key=lambda p: (abs(p.bar - price.bar), p.bar), default=None)
    if chosen:
        chosen.used = True
    return chosen


def detect(
    price: list[float],
    macd: list[float],
    rsi: list[float],
    *,
    high: bool,
    left: int = 1,
    right: int = 1,
    tolerance: int = 2,
    minimum_gap: int = 3,
    maximum_gap: int = 100,
    price_percent: float = 0,
    macd_minimum: float = 0,
    rsi_minimum: float = 0,
) -> list[dict]:
    """Replay mature price pivots using only information available on each event bar."""
    prices = pivots(price, left, right, high)
    oscillators = [pivots(macd, left, right, high), pivots(rsi, left, right, high)]
    previous_matches = [-1, -1]
    history = []
    events = []
    for current in prices:
        confirmed = current.confirmed + tolerance
        if confirmed >= len(price):
            continue
        matches = []
        for slot, candidates in enumerate(oscillators):
            found = match(current, candidates, tolerance, previous_matches[slot])
            matches.append(found)
            if found:
                previous_matches[slot] = found.bar
        selected = [None, None]
        for previous, earlier_matches in reversed(history):
            gap = current.bar - previous.bar
            if gap > maximum_gap:
                break
            if gap < minimum_gap:
                continue
            price_move = current.value - previous.value if high else previous.value - current.value
            if price_move <= 0 or price_move < abs(previous.value) * price_percent / 100:
                continue
            for slot, minimum in enumerate((macd_minimum, rsi_minimum)):
                earlier, later = earlier_matches[slot], matches[slot]
                if selected[slot] is not None or earlier is None or later is None:
                    continue
                change = earlier.value - later.value if high else later.value - earlier.value
                if change > 0 and change >= minimum:
                    selected[slot] = previous.bar
        if any(p is not None for p in selected):
            events.append(
                {
                    "bar": confirmed,
                    "price_bar": current.bar,
                    "macd_previous": selected[0],
                    "rsi_previous": selected[1],
                    "combined": selected[0] is not None and selected[0] == selected[1],
                }
            )
        history.append((current, matches))
    return events
