"""Structural divergence: gộp các phân kỳ local liên tiếp thành một cấu trúc lớn.

Local divergence (RSI/MACD) mô tả một cặp pivot. Khi nhiều cặp liên tiếp cùng
chiều, cùng loại, cùng chỉ báo và cùng nằm trong một cấu trúc giá, chúng tạo
thành một phân kỳ cấu trúc có ý nghĩa xu hướng mạnh hơn.

Quy tắc gộp (tất cả đều bắt buộc):

- Chuỗi local phải **liền mạch**: pivot kết thúc của local trước trùng đúng pivot
  bắt đầu của local sau. Không gộp chỉ vì các pivot gần nhau về thời gian.
- Cùng `direction`, cùng `kind` (regular/hidden) và cùng một chỉ báo.
- Pivot đầu và pivot cuối của chuỗi phải tự thoả điều kiện phân kỳ cho chỉ báo
  đó, và đoạn giá nối hai pivot không bị phá bởi giá chen giữa.
- Không nối hai cấu trúc xu hướng độc lập: đoạn giá phải "unobstructed" theo cùng
  quy tắc hình học dùng cho local divergence.

Module thuần tính toán trên mảng numpy và list event local, không truy cập
database, không phụ thuộc API schema.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

REGULAR: Final = "regular"
HIDDEN: Final = "hidden"
_EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class StructuralConfig:
    """Tham số gộp cấu trúc.

    Attributes:
        min_locals: Số local tối thiểu để tạo một cấu trúc (2 local = 3 pivot).
        max_pivots: Số pivot tối đa của một cấu trúc; chuỗi dài hơn sẽ bị cắt
            thành nhiều cấu trúc liên tiếp thay vì gộp vô hạn.
    """

    min_locals: int = 2
    max_pivots: int = 4

    def __post_init__(self) -> None:
        """Chặn cấu hình vô lý."""
        if self.min_locals < 2:
            raise ValueError("min_locals phải >= 2 để tạo phân kỳ cấu trúc")
        if self.max_pivots < self.min_locals + 1:
            raise ValueError("max_pivots phải >= min_locals + 1")


def frames_one_structure(prices: np.ndarray, start: int, end: int, *, high: bool) -> bool:
    """Kiểm tra hai pivot có bao trọn một cấu trúc giá hay không.

    Với hướng `high`, toàn bộ giá trong đoạn phải nằm dưới (hoặc bằng) đỉnh cao
    hơn của hai pivot; nếu có một đỉnh vượt lên thì đó là cấu trúc khác. Với
    hướng `low`, quy tắc đối xứng với đáy.

    Args:
        prices: Chuỗi giá cao nhất hoặc thấp nhất.
        start: Chỉ số pivot đầu.
        end: Chỉ số pivot cuối.
        high: True khi xét đỉnh, False khi xét đáy.

    Returns:
        True khi hai pivot bao trọn cấu trúc giá của đoạn.
    """
    if end <= start:
        return False
    segment = prices[start : end + 1]
    if not np.isfinite(segment).all():
        return False
    bound = max(prices[start], prices[end]) if high else min(prices[start], prices[end])
    extreme = float(segment.max() if high else segment.min())
    epsilon = max(1.0, abs(float(bound))) * _EPSILON
    return extreme <= bound + epsilon if high else extreme >= bound - epsilon


def _chain_is_valid(
    first: Mapping[str, Any],
    last: Mapping[str, Any],
    prices: np.ndarray,
    indicator: str,
) -> bool:
    """Kiểm tra pivot đầu và cuối của chuỗi có còn thoả phân kỳ không.

    Giá trị chỉ báo lấy từ chính các local event: engine dò pivot của chỉ báo độc
    lập với pivot giá (trong biên tolerance), nên không được đọc lại mảng chỉ báo
    tại chỉ số pivot giá.

    Args:
        first: Local event chứa pivot bắt đầu.
        last: Local event chứa pivot kết thúc.
        prices: Chuỗi giá dùng cho pivot (high hoặc low).
        indicator: Tên chỉ báo dùng để gộp (RSI hoặc MACD).

    Returns:
        True khi cặp pivot đầu–cuối là một phân kỳ hợp lệ cùng loại.
    """
    start, end = int(first["start_index"]), int(last["end_index"])
    if end <= start:
        return False
    price_values = prices[start : end + 1]
    if not np.isfinite(price_values).all():
        return False

    start_value = _indicator_value(first, indicator, edge="start")
    end_value = _indicator_value(last, indicator, edge="end")
    if start_value is None or end_value is None:
        return False

    sign = 1.0 if first["direction"] == "bearish" else -1.0
    price_delta = float(prices[end] - prices[start]) * sign
    line_delta = (end_value - start_value) * sign

    if first["kind"] == REGULAR:
        # Regular: giá tạo cực trị mới, chỉ báo thì không.
        if price_delta <= 0 or line_delta >= 0:
            return False
    else:
        # Hidden: giá không tạo cực trị mới, chỉ báo thì có.
        if price_delta >= 0 or line_delta <= 0:
            return False

    # Chỉ báo được phép zig-zag giữa hai pivot; điều kiện bắt buộc là hai pivot
    # cùng bao trọn một cấu trúc giá, không có cực trị chen giữa vượt ra ngoài.
    return frames_one_structure(prices, start, end, high=sign > 0)


def _indicator_value(event: Mapping[str, Any], indicator: str, *, edge: str) -> float | None:
    """Lấy giá trị chỉ báo tại pivot đầu hoặc cuối của một local event."""
    for oscillator in event.get("oscillators", []):
        if oscillator["indicator"] == indicator:
            key = "start_value" if edge == "start" else "end_value"
            return float(oscillator[key])
    return None


def _runs(positions: Sequence[int], events: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    """Tách danh sách local thành các chuỗi liền mạch theo pivot dùng chung."""
    runs: list[list[int]] = []
    current: list[int] = []
    for position in positions:
        if not current:
            current = [position]
            continue
        previous = events[current[-1]]
        if events[position]["start_index"] == previous["end_index"]:
            current.append(position)
        else:
            runs.append(current)
            current = [position]
    if current:
        runs.append(current)
    return runs


def group_structural_divergences(
    events: Sequence[Mapping[str, Any]],
    high: np.ndarray,
    low: np.ndarray,
    config: StructuralConfig | None = None,
) -> list[dict[str, Any]]:
    """Gộp các local divergence liên tiếp thành phân kỳ cấu trúc.

    Args:
        events: Local divergence theo thứ tự phát hiện, mỗi event có `kind`,
            `direction`, `start_index`, `end_index`, `confirmed_index` và
            `oscillators`.
        high: Chuỗi giá cao nhất, cùng độ dài dữ liệu dùng để phát hiện local.
        low: Chuỗi giá thấp nhất.
        config: Cấu hình gộp; mặc định `StructuralConfig()`.

    Returns:
        Danh sách cấu trúc, mỗi phần tử gồm `member_positions` (vị trí của các
        local thành phần trong `events`) cùng thông tin pivot đầu/cuối.
    """
    cfg = config or StructuralConfig()
    if len(events) < cfg.min_locals:
        return []

    used: set[int] = set()
    structures: list[dict[str, Any]] = []

    indicator_names = sorted({o["indicator"] for event in events for o in event["oscillators"]})
    for indicator in indicator_names:
        for direction in ("bearish", "bullish"):
            prices = high if direction == "bearish" else low
            for kind in (REGULAR, HIDDEN):
                positions = [
                    index
                    for index, event in enumerate(events)
                    if event["direction"] == direction
                    and event["kind"] == kind
                    and _indicator_value(event, indicator, edge="start") is not None
                    and _indicator_value(event, indicator, edge="end") is not None
                ]
                for run in _runs(positions, events):
                    cursor = 0
                    while cursor <= len(run) - cfg.min_locals:
                        longest = min(len(run) - cursor, cfg.max_pivots - 1)
                        chosen: list[int] | None = None
                        for size in range(longest, cfg.min_locals - 1, -1):
                            chain = run[cursor : cursor + size]
                            if any(position in used for position in chain):
                                continue
                            if _chain_is_valid(
                                events[chain[0]], events[chain[-1]], prices, indicator
                            ):
                                chosen = chain
                                break
                        if chosen is None:
                            cursor += 1
                            continue

                        first, last = events[chosen[0]], events[chosen[-1]]
                        used.update(chosen)
                        start, end = int(first["start_index"]), int(last["end_index"])
                        start_value = _indicator_value(first, indicator, edge="start")
                        end_value = _indicator_value(last, indicator, edge="end")
                        if start_value is None or end_value is None:
                            cursor += len(chosen)
                            continue
                        structures.append(
                            {
                                "kind": kind,
                                "direction": direction,
                                "indicator": indicator,
                                "start_index": start,
                                "end_index": end,
                                "start_price": float(first["start_price"]),
                                "end_price": float(last["end_price"]),
                                "start_value": start_value,
                                "end_value": end_value,
                                "pivot_count": len(chosen) + 1,
                                "confirmed_index": max(
                                    int(events[position]["confirmed_index"]) for position in chosen
                                ),
                                "price_confirmed_index": int(last["price_confirmed_index"]),
                                "member_positions": list(chosen),
                            }
                        )
                        cursor += len(chosen)

    return sorted(structures, key=lambda item: (item["confirmed_index"], item["start_index"]))
