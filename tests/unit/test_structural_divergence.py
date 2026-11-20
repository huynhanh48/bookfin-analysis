"""Structural divergence gộp local: chuỗi hợp lệ, cấu trúc giá và giới hạn pivot."""

from typing import Any

import numpy as np

from app.indicators.structural_divergence import (
    StructuralConfig,
    frames_one_structure,
    group_structural_divergences,
)


def local(
    *,
    kind: str = "regular",
    direction: str = "bearish",
    start: int,
    end: int,
    start_price: float,
    end_price: float,
    start_value: float,
    end_value: float,
    indicator: str = "RSI",
    confirmed: int | None = None,
) -> dict[str, Any]:
    """Tạo một local divergence tối thiểu đúng shape của engine."""
    return {
        "kind": kind,
        "direction": direction,
        "start_index": start,
        "end_index": end,
        "start_price": start_price,
        "end_price": end_price,
        "price_confirmed_index": end + 5,
        "confirmed_index": end + 8 if confirmed is None else confirmed,
        "oscillators": [
            {
                "indicator": indicator,
                "start_index": start,
                "end_index": end,
                "start_value": start_value,
                "end_value": end_value,
            }
        ],
    }


def prices_from(points: dict[int, float], size: int = 60, base: float = 100.0) -> np.ndarray:
    """Dựng chuỗi giá có các đỉnh/đáy tại vị trí chỉ định."""
    values = np.full(size, base, dtype=np.float64)
    for index, value in points.items():
        values[index] = value
    return values


def test_two_contiguous_regular_locals_form_one_structure():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=80, end_value=70),
        local(start=10, end=20, start_price=110, end_price=120, start_value=70, end_value=60),
    ]

    structures = group_structural_divergences(events, high, low)

    assert len(structures) == 1
    structure = structures[0]
    assert structure["kind"] == "regular"
    assert structure["direction"] == "bearish"
    assert structure["indicator"] == "RSI"
    assert structure["pivot_count"] == 3
    assert structure["member_positions"] == [0, 1]
    assert (structure["start_index"], structure["end_index"]) == (0, 20)
    # Xác nhận cấu trúc chỉ khi mọi local thành phần đã được xác nhận.
    assert structure["confirmed_index"] == max(e["confirmed_index"] for e in events)
    assert structure["confirmed_index"] > structure["end_index"]


def test_two_contiguous_hidden_locals_form_hidden_structure():
    high = prices_from({0: 120.0, 10: 110.0, 20: 100.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(
            kind="hidden",
            start=0,
            end=10,
            start_price=120,
            end_price=110,
            start_value=60,
            end_value=65,
        ),
        local(
            kind="hidden",
            start=10,
            end=20,
            start_price=110,
            end_price=100,
            start_value=65,
            end_value=70,
        ),
    ]

    structures = group_structural_divergences(events, high, low)

    assert len(structures) == 1
    assert structures[0]["kind"] == "hidden"
    assert structures[0]["pivot_count"] == 3


def test_non_contiguous_locals_are_not_grouped():
    high = prices_from({0: 100.0, 10: 110.0, 12: 115.0, 22: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=80, end_value=70),
        local(start=12, end=22, start_price=115, end_price=120, start_value=70, end_value=60),
    ]

    assert group_structural_divergences(events, high, low) == []


def test_locals_with_different_indicators_are_not_grouped():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(
            start=0,
            end=10,
            start_price=100,
            end_price=110,
            start_value=80,
            end_value=70,
            indicator="MACD",
        ),
        local(
            start=10,
            end=20,
            start_price=110,
            end_price=120,
            start_value=70,
            end_value=60,
            indicator="RSI",
        ),
    ]

    assert group_structural_divergences(events, high, low) == []


def test_locals_with_different_kind_are_not_grouped():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=80, end_value=70),
        local(
            kind="hidden",
            start=10,
            end=20,
            start_price=110,
            end_price=120,
            start_value=70,
            end_value=60,
        ),
    ]

    assert group_structural_divergences(events, high, low) == []


def test_first_and_last_pivot_must_keep_diverging():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=80, end_value=60),
        # RSI cuối cao hơn RSI đầu -> A..C không còn là regular bearish.
        local(start=10, end=20, start_price=110, end_price=120, start_value=60, end_value=85),
    ]

    assert group_structural_divergences(events, high, low) == []


def test_structure_break_between_pivots_is_rejected():
    # Đỉnh 130 chen giữa vượt cả hai pivot -> hai cấu trúc khác nhau.
    high = prices_from({0: 100.0, 10: 110.0, 15: 130.0, 20: 120.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=80, end_value=70),
        local(start=10, end=20, start_price=110, end_price=120, start_value=70, end_value=60),
    ]

    assert group_structural_divergences(events, high, low) == []


def test_three_locals_are_limited_by_max_pivots():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0, 30: 130.0, 40: 140.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=90, end_value=80),
        local(start=10, end=20, start_price=110, end_price=120, start_value=80, end_value=70),
        local(start=20, end=30, start_price=120, end_price=130, start_value=70, end_value=60),
        local(start=30, end=40, start_price=130, end_price=140, start_value=60, end_value=50),
    ]

    structures = group_structural_divergences(events, high, low, StructuralConfig(max_pivots=4))

    assert len(structures) == 1
    assert structures[0]["member_positions"] == [0, 1, 2]
    assert structures[0]["pivot_count"] == 4


def test_each_local_belongs_to_at_most_one_structure():
    high = prices_from({0: 100.0, 10: 110.0, 20: 120.0, 30: 130.0}, base=95.0)
    low = np.full(60, 90.0)
    events = [
        local(start=0, end=10, start_price=100, end_price=110, start_value=90, end_value=80),
        local(start=10, end=20, start_price=110, end_price=120, start_value=80, end_value=70),
        local(start=20, end=30, start_price=120, end_price=130, start_value=70, end_value=60),
    ]

    structures = group_structural_divergences(events, high, low, StructuralConfig(max_pivots=3))

    members = [position for structure in structures for position in structure["member_positions"]]
    assert len(members) == len(set(members))


def test_frames_one_structure_uses_the_outer_extreme():
    prices = np.array([100.0, 120.0, 110.0], dtype=np.float64)
    # Đỉnh 120 chen giữa vượt đỉnh cuối 110 -> cấu trúc đỉnh bị phá.
    assert not frames_one_structure(prices, 0, 2, high=True)
    assert frames_one_structure(prices, 1, 2, high=True)
    # Xét đáy: đáy thấp nhất nằm ở pivot đầu nên vẫn bao trọn cấu trúc.
    assert frames_one_structure(prices, 0, 2, high=False)

    lows = np.array([90.0, 70.0, 80.0], dtype=np.float64)
    assert frames_one_structure(lows, 0, 1, high=False)
    assert frames_one_structure(lows, 1, 2, high=False)
    assert not frames_one_structure(lows, 0, 2, high=False)
