"""Offline behavior specification; these tests do NOT compile or execute Pine."""

from pathlib import Path

import pytest
from tools.swing_divergence_reference import Pivot, detect, match


def series(first, second, high=True):
    """Two isolated extrema separated by eight bars, enough final confirmation bars."""
    values = [0.0] * 18 if high else [100.0] * 18
    values[3], values[11] = first, second
    return values


@pytest.mark.parametrize(
    ("indicator", "high"), [("macd", True), ("macd", False), ("rsi", True), ("rsi", False)]
)
def test_four_required_regular_divergences(indicator, high):
    price = series(10, 12) if high else series(90, 88, False)
    oscillator = series(7, 5) if high else series(20, 25, False)
    absent = [float("nan")] * len(price)
    events = detect(
        price,
        oscillator if indicator == "macd" else absent,
        oscillator if indicator == "rsi" else absent,
        high=high,
    )
    assert len(events) == 1
    assert events[0][f"{indicator}_previous"] == 3
    assert events[0]["bar"] == 14  # pivot 11 + right 1 + tolerance 2
    assert not events[0]["combined"]


def test_joint_same_pair_and_no_future_prefix():
    price, oscillator = series(10, 12), series(7, 5)
    events = detect(price, oscillator, oscillator, high=True)
    assert events[0]["combined"]
    for end in range(1, len(price) + 1):
        assert detect(price[:end], oscillator[:end], oscillator[:end], high=True) == [
            event for event in events if event["bar"] < end
        ]


def test_matching_is_bounded_unique_and_confirmed():
    candidates = [Pivot(2, 7, 3), Pivot(4, 8, 5), Pivot(20, 9, 21)]
    chosen = match(Pivot(3, 10, 4), candidates, 1, -1)
    assert chosen.bar == 2  # older tie wins
    assert match(Pivot(3, 11, 4), candidates, 1, 2).bar == 4
    assert match(Pivot(3, 12, 4), candidates, 1, 4) is None
    assert match(Pivot(10, 13, 11), [Pivot(12, 9, 20)], 2, -1) is None


def test_threshold_gap_equal_and_missing_data_filters():
    price, oscillator = series(10, 12), series(7, 5)
    assert not detect(price, oscillator, oscillator, high=True, maximum_gap=7)
    assert not detect(price, oscillator, oscillator, high=True, minimum_gap=9)
    assert not detect(price, oscillator, oscillator, high=True, price_percent=21)
    assert not detect(price, oscillator, oscillator, high=True, macd_minimum=3, rsi_minimum=3)
    assert not detect(series(10, 10), oscillator, oscillator, high=True)
    assert not detect(price, series(7, 7), series(7, 7), high=True)
    assert not detect(price, [float("nan")] * 18, [float("nan")] * 18, high=True)


def test_pine_has_six_closed_bar_alerts_and_separate_original_smc():
    source = Path("indicators/pine/smc_swing_divergence_v5.pine").read_text()
    assert source.startswith("//@version=5\n")
    assert source.count("alertcondition(") == 6
    assert "bar_index < current.bar + divRight + divTolerance" in source
    assert "barstate.isconfirmed" in source
    assert "macdIndex == rsiIndex" in source
    assert "trend := 1" in source and "trend := -1" in source
    assert "breakUp   := breakUp   and not highBroken and not gotPH" in source


def test_independent_oscillators_do_not_merge_different_pairs():
    price, macd, rsi = [0.0] * 26, [0.0] * 26, [0.0] * 26
    for bar, p, m, r in [(3, 10, 7, 9), (11, 9, 8, 5), (19, 12, 6, 6)]:
        price[bar], macd[bar], rsi[bar] = p, m, r
    last = detect(price, macd, rsi, high=True)[-1]
    assert last["macd_previous"] == 11
    assert last["rsi_previous"] == 3
    assert not last["combined"]


def test_shifted_oscillator_pivots_use_tolerance_window():
    price = series(10, 12)
    shifted = [0.0] * len(price)
    shifted[5], shifted[10] = 7, 5
    assert detect(price, shifted, shifted, high=True, tolerance=2)[0]["combined"]
    assert not detect(price, shifted, shifted, high=True, tolerance=0)
