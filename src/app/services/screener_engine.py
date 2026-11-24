"""Screener engine thuần Python cho chiến lược lọc dòng tiền AFL.

Công thức gốc trong `docs/loc_dong_tien.afl`:

    DK1 = V > 100000
    DK2 = C > 10
    DK3 = V > 0.8 * MA(Ref(V,-1), 10)
    DK4 = C > 1.02 * Ref(C,-1)
    DK6 = C > 1.02 * MA(C, 5)
    Buy = DK1 AND DK2 AND DK3 AND DK4 AND DK6

Giá FCData là VND, nên `C > 10` của AFL tương đương `close > 10_000` VND.
DK3 dùng đúng 10 phiên trước, không gồm phiên hiện tại. DK6 dùng MA5 gồm cả
phiên hiện tại.

Engine không biết FastAPI, SQLAlchemy hay Redis để dễ unit test và tái sử dụng.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np

from app.indicators.divergence import divergence_history
from app.indicators.market_structure import CandleBar
from app.indicators.swing_divergence import DivergenceConfig
from app.indicators.trend import compute_sma
from app.indicators.volume import compute_volume_sma
from app.schemas.screener import ScreenerConditions, ScreenerStatus


@dataclass(frozen=True, slots=True)
class DivergenceEventSummary:
    """Phân kỳ đã xác nhận, đủ nhỏ để đưa vào screener snapshot."""

    kind: str
    direction: str
    indicators: tuple[str, ...]
    end_index: int
    confirmed_index: int


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    """Dữ liệu đã chuẩn hoá của một symbol tại thời điểm đánh giá.

    Attributes:
        symbol: Mã chứng khoán.
        history: Các nến đã đóng trước bar hiện tại, tăng dần theo thời gian.
        current: Bar hiện tại; có thể là nến confirmed hoặc provisional realtime.
        status: LIVE, CONFIRMED hoặc STALE.
        updated_at: Thời điểm dữ liệu hiện tại.
        divergence_events: Các phân kỳ đã xác nhận trong cửa sổ gần nhất.
    """

    symbol: str
    history: tuple[CandleBar, ...]
    current: CandleBar
    status: ScreenerStatus
    updated_at: datetime
    divergence_events: tuple[DivergenceEventSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """Kết quả một điều kiện."""

    key: str
    matched: bool
    value: float | None = None
    threshold: float | None = None
    divergence: DivergenceEventSummary | None = None


@dataclass(frozen=True, slots=True)
class ScreenerMatch:
    """Một symbol thỏa toàn bộ điều kiện đang bật."""

    symbol: str
    price: float
    change_percent: float
    volume: int
    volume_sma: float
    relative_volume: float
    ma5: float
    price_vs_ma5_percent: float
    status: ScreenerStatus
    updated_at: datetime
    matched_conditions: tuple[str, ...]
    divergence: DivergenceEventSummary | None = None


class ScreenerRule(Protocol):
    """Giao diện mở rộng cho các điều kiện mới (RSI, MACD, ADX, ...)."""

    key: str

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome: ...


def _previous_close(snapshot: SymbolSnapshot) -> float | None:
    """Close phiên đã đóng gần nhất trước bar hiện tại."""
    if not snapshot.history:
        return None
    return float(snapshot.history[-1].close)


def _current_volume(snapshot: SymbolSnapshot) -> float:
    """Volume tích luỹ của bar hiện tại."""
    return float(snapshot.current.volume)


class MinVolumeRule:
    """DK1: volume hiện tại lớn hơn ngưỡng."""

    key = "DK1"

    def __init__(self, minimum: int) -> None:
        self._minimum = float(minimum)

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        volume = _current_volume(snapshot)
        return RuleOutcome(self.key, volume > self._minimum, volume, self._minimum)


class MinPriceRule:
    """DK2: giá hiện tại lớn hơn ngưỡng VND."""

    key = "DK2"

    def __init__(self, minimum: float) -> None:
        self._minimum = float(minimum)

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        price = float(snapshot.current.close)
        return RuleOutcome(self.key, price > self._minimum, price, self._minimum)


class RelativeVolumeRule:
    """DK3: volume hiện tại > ratio * SMA(volume 10 phiên trước)."""

    key = "DK3"

    def __init__(self, ratio: float, lookback: int) -> None:
        self._ratio = float(ratio)
        self._lookback = int(lookback)

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        if len(snapshot.history) < self._lookback:
            return RuleOutcome(self.key, False, None, None)
        previous = np.asarray(
            [float(bar.volume) for bar in snapshot.history[-self._lookback :]],
            dtype=np.float64,
        )
        average = compute_volume_sma(previous, period=self._lookback).values.get(
            "volume_sma"
        )
        if average is None or not np.isfinite(average) or average <= 0:
            return RuleOutcome(self.key, False, None, None)
        threshold = self._ratio * float(average)
        volume = _current_volume(snapshot)
        return RuleOutcome(self.key, volume > threshold, volume, threshold)


class PriceChangeRule:
    """DK4: close hiện tại > (1 + percent/100) * close phiên trước."""

    key = "DK4"

    def __init__(self, percent: float) -> None:
        self._percent = float(percent)

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        previous = _previous_close(snapshot)
        if previous is None or previous <= 0:
            return RuleOutcome(self.key, False, None, None)
        threshold = previous * (1 + self._percent / 100)
        price = float(snapshot.current.close)
        return RuleOutcome(self.key, price > threshold, price, threshold)


class PriceVsMARule:
    """DK6: close hiện tại > (1 + percent/100) * SMA(close, period)."""

    key = "DK6"

    def __init__(self, percent: float, period: int) -> None:
        self._percent = float(percent)
        self._period = int(period)

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        required_history = self._period - 1
        if len(snapshot.history) < required_history:
            return RuleOutcome(self.key, False, None, None)
        closes = [
            *(float(bar.close) for bar in snapshot.history[-required_history:]),
            float(snapshot.current.close),
        ]
        average = compute_sma(np.asarray(closes, dtype=np.float64), period=self._period).values.get(
            "sma"
        )
        if average is None or not np.isfinite(average) or average <= 0:
            return RuleOutcome(self.key, False, None, None)
        threshold = float(average) * (1 + self._percent / 100)
        price = float(snapshot.current.close)
        return RuleOutcome(self.key, price > threshold, price, threshold)


class DivergenceRule:
    """Lọc event phân kỳ đã xác nhận trong cửa sổ gần nhất."""

    key = "DIVERGENCE"

    def __init__(self, direction: str, kind: str) -> None:
        self._direction = direction
        self._kind = kind

    def evaluate(self, snapshot: SymbolSnapshot) -> RuleOutcome:
        candidates = [
            event
            for event in snapshot.divergence_events
            if (self._direction == "any" or event.direction == self._direction)
            and (self._kind == "any" or event.kind == self._kind)
        ]
        if not candidates:
            return RuleOutcome(self.key, False)
        latest = max(candidates, key=lambda event: event.confirmed_index)
        return RuleOutcome(self.key, True, divergence=latest)


class ScreenerEngine:
    """Đánh giá 5 điều kiện AFL trên một `SymbolSnapshot`."""

    def evaluate(
        self, snapshot: SymbolSnapshot, conditions: ScreenerConditions
    ) -> ScreenerMatch | None:
        """Trả match nếu tất cả điều kiện đang bật đều đúng.

        Args:
            snapshot: Dữ liệu symbol.
            conditions: Cấu hình điều kiện và công tắc bật/tắt.

        Returns:
            `ScreenerMatch` hoặc None khi thiếu dữ liệu/không thỏa.
        """
        current = snapshot.current
        if not np.isfinite([current.close, current.volume]).all():
            return None
        if current.close <= 0 or current.volume < 0:
            return None

        previous = _previous_close(snapshot)
        if previous is None or previous <= 0:
            return None

        rules: list[ScreenerRule] = []
        if conditions.min_volume.enabled:
            rules.append(MinVolumeRule(conditions.min_volume.value))
        if conditions.min_price.enabled:
            rules.append(MinPriceRule(conditions.min_price.value))
        if conditions.relative_volume.enabled:
            rules.append(
                RelativeVolumeRule(
                    conditions.relative_volume.value,
                    conditions.relative_volume.lookback,
                )
            )
        if conditions.price_change.enabled:
            rules.append(PriceChangeRule(conditions.price_change.percent))
        if conditions.price_vs_ma5.enabled:
            rules.append(
                PriceVsMARule(
                    conditions.price_vs_ma5.percent,
                    conditions.price_vs_ma5.period,
                )
            )
        if conditions.divergence.enabled:
            rules.append(
                DivergenceRule(
                    conditions.divergence.direction,
                    conditions.divergence.kind,
                )
            )

        outcomes = [rule.evaluate(snapshot) for rule in rules]
        if not outcomes or any(not outcome.matched for outcome in outcomes):
            return None
        by_key = {outcome.key: outcome for outcome in outcomes}

        # Metrics hiển thị luôn được tính kể cả khi điều kiện tương ứng bị tắt,
        # để frontend vẫn có Vol/SMA10 và MA5 trong bảng kết quả.
        lookback = conditions.relative_volume.lookback
        volume_sma = 0.0
        if len(snapshot.history) >= lookback:
            previous_volumes = np.asarray(
                [float(bar.volume) for bar in snapshot.history[-lookback:]],
                dtype=np.float64,
            )
            average = compute_volume_sma(
                previous_volumes, period=lookback
            ).values.get("volume_sma")
            if average is not None and np.isfinite(average) and average > 0:
                volume_sma = float(average)
        relative_volume = float(current.volume) / volume_sma if volume_sma > 0 else 0.0

        ma_period = conditions.price_vs_ma5.period
        ma5 = 0.0
        if len(snapshot.history) >= ma_period - 1:
            closes = [
                *(float(bar.close) for bar in snapshot.history[-(ma_period - 1) :]),
                float(current.close),
            ]
            average = compute_sma(
                np.asarray(closes, dtype=np.float64), period=ma_period
            ).values.get("sma")
            if average is not None and np.isfinite(average) and average > 0:
                ma5 = float(average)

        change_percent = (float(current.close) / previous - 1) * 100
        price_vs_ma5_percent = (
            (float(current.close) / ma5 - 1) * 100 if ma5 > 0 else 0.0
        )

        divergence_outcome = by_key.get("DIVERGENCE")
        return ScreenerMatch(
            symbol=snapshot.symbol,
            price=float(current.close),
            change_percent=change_percent,
            volume=int(current.volume),
            volume_sma=float(volume_sma),
            relative_volume=float(relative_volume),
            ma5=float(ma5),
            price_vs_ma5_percent=price_vs_ma5_percent,
            status=snapshot.status,
            updated_at=snapshot.updated_at,
            matched_conditions=tuple(outcome.key for outcome in outcomes),
            divergence=(
                divergence_outcome.divergence
                if divergence_outcome is not None
                else None
            ),
        )


def extract_divergence_events(
    bars: Sequence[CandleBar],
    *,
    days: int,
    basis: str = "pivot",
    date_from: date | None = None,
    date_to: date | None = None,
    market_tz: ZoneInfo | None = None,
) -> tuple[DivergenceEventSummary, ...]:
    """Tính phân kỳ đã xác nhận trên chuỗi nến đóng và lọc theo ngày/cửa sổ.

    Args:
        bars: Nến đã đóng, tăng dần theo thời gian. Không truyền nến đang chạy.
        days: Số nến cuối khi không truyền date_from/date_to.
        basis: `pivot` dùng đỉnh/đáy thứ hai; `confirmed` dùng nến xác nhận.
        date_from: Ngày mốc bắt đầu inclusive.
        date_to: Ngày mốc kết thúc inclusive.
        market_tz: Múi giờ để quy đổi thời điểm xác nhận sang ngày giao dịch.

    Returns:
        Các event đã chuẩn hoá; rỗng nếu không đủ dữ liệu.
    """
    if len(bars) < 30:
        return ()
    high = np.asarray([bar.high for bar in bars], dtype=np.float64)
    low = np.asarray([bar.low for bar in bars], dtype=np.float64)
    close = np.asarray([bar.close for bar in bars], dtype=np.float64)
    events = divergence_history(high, low, close, DivergenceConfig())
    threshold = max(0, len(bars) - days)
    use_dates = date_from is not None or date_to is not None
    tz = market_tz
    selected: list[DivergenceEventSummary] = []
    for event in events:
        end_index = int(event["end_index"])
        confirmed_index = int(event["confirmed_index"])
        index = end_index if basis == "pivot" else confirmed_index
        if not 0 <= index < len(bars):
            continue
        if use_dates:
            event_day = bars[index].time.astimezone(tz).date() if tz else bars[index].time.date()
            if date_from is not None and event_day < date_from:
                continue
            if date_to is not None and event_day > date_to:
                continue
        elif index < threshold:
            continue
        selected.append(
            DivergenceEventSummary(
                kind=str(event["kind"]),
                direction=str(event["direction"]),
                indicators=tuple(
                    str(item["indicator"]) for item in event.get("oscillators", [])
                ),
                end_index=end_index,
                confirmed_index=confirmed_index,
            )
        )
    return tuple(selected)
