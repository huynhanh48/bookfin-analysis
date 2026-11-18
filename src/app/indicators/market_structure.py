"""Cấu trúc thị trường: swing, HH/HL/LH/LL, BOS và CHoCH.

Thuật toán được chuyển từ script Pine tham chiếu trong `references/smc.txt`
(SMC Market Structure, Pine v5) sang Python thuần. Engine không phụ thuộc FastAPI,
Redis hay PostgreSQL: đầu vào là danh sách nến **đã đóng**, đầu ra là
`StructureSnapshot`.

Quy tắc cốt lõi (chế độ `pine_compatible`):

- Pivot high/low dùng cùng cửa sổ trái/phải bằng `pivot_length`. Một pivot tại bar
  `i` chỉ được biết tại bar `i + pivot_length`, nên engine không bao giờ dùng dữ
  liệu tương lai.
- Sau khi cập nhật pivot, nếu mức swing chưa bị phá và giá đóng cửa vượt mức đó
  thì phát BOS (phá tiếp xu hướng) hoặc CHoCH (phá ngược xu hướng hiện tại).
- `break_bias` mô phỏng biến `trend` của Pine: đổi ngay khi có phá mức. Đây
  **không** phải xác nhận đảo chiều. `confirmed_trend` chỉ lên UPTREND khi có
  HH+HL đã xác nhận, DOWNTREND khi có LH+LL.

Khác biệt có chủ ý so với file Pine tham chiếu (đã ghi rõ để đối chiếu):

- File Pine mặc định `breakConfirm = "Wick"`; đặc tả dự án dùng giá đóng cửa nên
  mặc định ở đây là `break_confirmation="close"`. Đặt `"wick"` nếu muốn khớp
  mặc định của file Pine.
- Pine có thêm điều kiện `close[1] <= lastHigh` và `not gotPH`. Hai điều kiện này
  chỉ bật khi đặt `require_crossing=True` và `suppress_break_on_new_pivot=True`.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import numpy as np
import talib

ENGINE_VERSION = "ms-1.0.0"


class PivotType(StrEnum):
    """Loại pivot."""

    HIGH = "HIGH"
    LOW = "LOW"


class SwingLabel(StrEnum):
    """Nhãn so sánh giữa hai pivot liên tiếp cùng loại."""

    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    EQH = "EQH"
    EQL = "EQL"


class BreakBias(StrEnum):
    """Bias phá cấu trúc, tương đương biến `trend` trong Pine."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class ConfirmedTrend(StrEnum):
    """Xu hướng chỉ được xác nhận khi cấu trúc swing đã hoàn chỉnh."""

    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    SIDEWAYS = "SIDEWAYS"
    TRANSITION = "TRANSITION"


class StructureEventType(StrEnum):
    """Loại sự kiện cấu trúc."""

    BOS_BULLISH = "BOS_BULLISH"
    BOS_BEARISH = "BOS_BEARISH"
    CHOCH_BULLISH = "CHOCH_BULLISH"
    CHOCH_BEARISH = "CHOCH_BEARISH"
    MSS_BULLISH = "MSS_BULLISH"
    MSS_BEARISH = "MSS_BEARISH"
    FAILED_BREAKOUT_BULLISH = "FAILED_BREAKOUT_BULLISH"
    FAILED_BREAKOUT_BEARISH = "FAILED_BREAKOUT_BEARISH"


@dataclass(frozen=True, slots=True)
class CandleBar:
    """Một nến đã đóng ở dạng engine cần.

    Attributes:
        time: Thời điểm mở nến.
        open: Giá mở cửa.
        high: Giá cao nhất.
        low: Giá thấp nhất.
        close: Giá đóng cửa.
        volume: Khối lượng; 0 nghĩa là nguồn không trả về.
    """

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True, slots=True)
class MarketStructureConfig:
    """Cấu hình engine cấu trúc thị trường.

    Attributes:
        pivot_length: Số nến trái và phải của một pivot.
        mode: `pine_compatible` giữ đúng hành vi mặc định; `enhanced` cho phép
            bật các bộ lọc mở rộng.
        break_confirmation: `close` dùng giá đóng cửa, `wick` dùng giá cao/thấp
            nhất để xác nhận phá mức.
        require_crossing: Chỉ tính phá mức khi nến trước còn ở dưới/trên mức đó.
        suppress_break_on_new_pivot: Bỏ qua kiểm tra phá mức ngay tại bar vừa xác
            nhận pivot mới, giống điều kiện `not gotPH` của Pine.
        equal_tolerance_pct: Ngưỡng phần trăm coi hai đỉnh/đáy là bằng nhau (EQH/EQL).
        breakout_buffer_atr: Hệ số ATR cộng thêm vào mức phá (bộ lọc mở rộng).
        atr_period: Chu kỳ ATR dùng cho buffer và displacement.
        displacement_atr_multiple: Ngưỡng thân nến để coi là displacement.
        emit_mss: Phát MSS thay cho CHoCH khi nến phá có displacement.
        detect_failed_breakout: Bật phát hiện phá hụt sau khi phá.
        failed_breakout_window: Số nến tối đa để xác nhận phá hụt.
    """

    pivot_length: int = 5
    mode: Literal["pine_compatible", "enhanced"] = "pine_compatible"
    break_confirmation: Literal["close", "wick"] = "close"
    require_crossing: bool = False
    suppress_break_on_new_pivot: bool = False
    equal_tolerance_pct: float = 0.0
    breakout_buffer_atr: float = 0.0
    atr_period: int = 14
    displacement_atr_multiple: float = 1.5
    emit_mss: bool = False
    detect_failed_breakout: bool = False
    failed_breakout_window: int = 3

    def __post_init__(self) -> None:
        """Kiểm tra ràng buộc giữa chế độ và các bộ lọc mở rộng.

        Raises:
            ValueError: Khi chế độ `pine_compatible` được đặt kèm bộ lọc mở rộng,
                hoặc khi tham số không hợp lệ.
        """
        if self.pivot_length < 1:
            raise ValueError("pivot_length phải >= 1")
        if self.mode == "pine_compatible":
            enhanced = {
                "equal_tolerance_pct": self.equal_tolerance_pct,
                "breakout_buffer_atr": self.breakout_buffer_atr,
                "emit_mss": self.emit_mss,
                "detect_failed_breakout": self.detect_failed_breakout,
            }
            enabled = [name for name, value in enhanced.items() if value not in (0, 0.0, False)]
            if enabled:
                raise ValueError(
                    "Chế độ pine_compatible không nhận bộ lọc mở rộng: " + ", ".join(enabled)
                )

    def to_dict(self) -> dict[str, Any]:
        """Trả cấu hình dưới dạng dict để tính hash."""
        return {
            "pivot_length": self.pivot_length,
            "mode": self.mode,
            "break_confirmation": self.break_confirmation,
            "require_crossing": self.require_crossing,
            "suppress_break_on_new_pivot": self.suppress_break_on_new_pivot,
            "equal_tolerance_pct": self.equal_tolerance_pct,
            "breakout_buffer_atr": self.breakout_buffer_atr,
            "atr_period": self.atr_period,
            "displacement_atr_multiple": self.displacement_atr_multiple,
            "emit_mss": self.emit_mss,
            "detect_failed_breakout": self.detect_failed_breakout,
            "failed_breakout_window": self.failed_breakout_window,
        }

    @property
    def config_hash(self) -> str:
        """Hash 12 ký tự của cấu hình, dùng làm khoá idempotency."""
        payload = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """Một pivot đã được xác nhận.

    Attributes:
        kind: HIGH hoặc LOW.
        label: HH/HL/LH/LL hoặc EQH/EQL; None khi chưa có pivot trước để so sánh.
        price: Giá tại pivot.
        pivot_time: Thời điểm bar tạo pivot.
        confirmed_at: Thời điểm bar xác nhận pivot.
        index: Vị trí bar tạo pivot trong chuỗi đầu vào.
    """

    kind: PivotType
    label: SwingLabel | None
    price: float
    pivot_time: datetime
    confirmed_at: datetime
    index: int


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """Một sự kiện phá cấu trúc.

    Attributes:
        event_type: Loại sự kiện.
        pivot_time: Thời điểm bar tạo swing bị phá.
        confirmed_at: Thời điểm swing được xác nhận.
        broken_at: Thời điểm bar đóng phá mức swing.
        price: Giá tại swing bị phá.
        break_level: Mức giá bị phá (đã cộng buffer nếu bật).
        observed_price: Giá dùng để xác nhận phá.
        bias_before: Bias trước sự kiện.
        bias_after: Bias sau sự kiện.
        is_displacement: True khi thân nến phá lớn hơn ATR × hệ số.
    """

    event_type: StructureEventType
    pivot_time: datetime
    confirmed_at: datetime
    broken_at: datetime
    price: float
    break_level: float
    observed_price: float
    bias_before: BreakBias
    bias_after: BreakBias
    is_displacement: bool = False


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    """Ảnh chụp cấu trúc thị trường tại nến đã đóng cuối cùng.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến cuối cùng.
        break_bias: Bias phá cấu trúc.
        confirmed_trend: Xu hướng đã xác nhận theo cấu trúc swing.
        last_swing_high: Swing high gần nhất.
        last_swing_low: Swing low gần nhất.
        protected_high: Kháng cự chưa bị phá gần nhất.
        protected_low: Hỗ trợ chưa bị phá gần nhất.
        swings: Các swing đã xác nhận.
        events: Các sự kiện đã xác nhận.
        latest_event: Sự kiện gần nhất.
        engine_version: Phiên bản engine.
        config_hash: Hash cấu hình đã dùng.
        candle_finality: closed khi mọi nến đầu vào đã đóng.
        data_quality: Thống kê kiểm tra chất lượng dữ liệu đầu vào.
    """

    symbol: str
    timeframe: str
    as_of: datetime | None
    break_bias: BreakBias
    confirmed_trend: ConfirmedTrend
    last_swing_high: SwingPoint | None
    last_swing_low: SwingPoint | None
    protected_high: SwingPoint | None
    protected_low: SwingPoint | None
    swings: tuple[SwingPoint, ...]
    events: tuple[StructureEvent, ...]
    latest_event: StructureEvent | None
    engine_version: str
    config_hash: str
    candle_finality: Literal["closed", "forming"]
    data_quality: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Literal["ok", "insufficient_history"]:
        """ok khi engine có đủ dữ liệu để xác định ít nhất một pivot."""
        return "insufficient_history" if self.data_quality.get("insufficient_history") else "ok"


def find_pivot_highs(highs: Sequence[float], left: int, right: int) -> list[int]:
    """Tìm index các pivot high theo cùng semantics với `ta.pivothigh`.

    Một bar là pivot high khi giá cao nhất của nó lớn hơn mọi bar trong `left` bar
    bên trái và `right` bar bên phải. Pivot chỉ được biết tại bar `i + right`.

    Args:
        highs: Chuỗi giá cao nhất.
        left: Số bar bên trái.
        right: Số bar bên phải.

    Returns:
        Danh sách index pivot, tăng dần.
    """
    if left < 1 or right < 1:
        raise ValueError("left và right phải >= 1")
    values = np.asarray(highs, dtype=np.float64)
    pivots: list[int] = []
    for index in range(left, values.size - right):
        candidate = values[index]
        if (
            candidate > values[index - left : index].max()
            and candidate > values[index + 1 : index + right + 1].max()
        ):
            pivots.append(index)
    return pivots


def find_pivot_lows(lows: Sequence[float], left: int, right: int) -> list[int]:
    """Tìm index các pivot low theo cùng semantics với `ta.pivotlow`.

    Args:
        lows: Chuỗi giá thấp nhất.
        left: Số bar bên trái.
        right: Số bar bên phải.

    Returns:
        Danh sách index pivot, tăng dần.
    """
    if left < 1 or right < 1:
        raise ValueError("left và right phải >= 1")
    values = np.asarray(lows, dtype=np.float64)
    pivots: list[int] = []
    for index in range(left, values.size - right):
        candidate = values[index]
        if (
            candidate < values[index - left : index].min()
            and candidate < values[index + 1 : index + right + 1].min()
        ):
            pivots.append(index)
    return pivots


def _is_valid_bar(bar: CandleBar) -> bool:
    """Kiểm tra một nến có hợp lệ về mặt số học.

    Args:
        bar: Nến cần kiểm tra.

    Returns:
        True khi mọi giá hữu hạn, high >= low và open/close nằm trong [low, high].
    """
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(value is None or not np.isfinite(value) for value in prices):
        return False
    if bar.high < bar.low:
        return False
    if not bar.low <= bar.open <= bar.high:
        return False
    return bar.low <= bar.close <= bar.high


class MarketStructureEngine:
    """Engine cấu trúc thị trường, xử lý từng nến đã đóng.

    Engine giữ trạng thái nên vừa dùng được cho batch (nạp cả lịch sử) vừa dùng
    được cho incremental (nạp từng nến mới). Kết quả hai cách chạy là như nhau vì
    cùng đi qua một hàm `feed`.
    """

    def __init__(self, config: MarketStructureConfig | None = None) -> None:
        """Khởi tạo engine với cấu hình cho trước.

        Args:
            config: Cấu hình engine; mặc định là chế độ `pine_compatible`.
        """
        self._config = config or MarketStructureConfig()
        self.reset()

    @property
    def config(self) -> MarketStructureConfig:
        """Cấu hình engine đang dùng."""
        return self._config

    def reset(self) -> None:
        """Xoá toàn bộ trạng thái để bắt đầu một chuỗi mới."""
        self._times: list[datetime] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._opens: list[float] = []
        self._volumes: list[float] = []
        self._last_high: float | None = None
        self._last_high_index: int | None = None
        self._last_low: float | None = None
        self._last_low_index: int | None = None
        self._last_label_high: SwingLabel | None = None
        self._last_label_low: SwingLabel | None = None
        self._high_broken = True
        self._low_broken = True
        self._bias = BreakBias.NEUTRAL
        self._trend = ConfirmedTrend.SIDEWAYS
        self._swings: list[SwingPoint] = []
        self._events: list[StructureEvent] = []
        self._pending_break: tuple[StructureEventType, float, int] | None = None
        self._quality: dict[str, Any] = {
            "bars": 0,
            "invalid_bars": 0,
            "duplicate_or_out_of_order": 0,
            "missing_volume": 0,
            "insufficient_history": False,
        }
        self._atr_cache: tuple[int, float | None] | None = None

    def feed(self, bar: CandleBar) -> tuple[StructureEvent, ...]:
        """Nạp một nến đã đóng và trả các sự kiện phát sinh tại nến đó.

        Args:
            bar: Nến đã đóng, phải có thời gian tăng dần.

        Returns:
            Tuple sự kiện mới; rỗng khi nến bị bỏ qua hoặc không có sự kiện.
        """
        self._quality["bars"] += 1

        if self._times and bar.time <= self._times[-1]:
            self._quality["duplicate_or_out_of_order"] += 1
            return ()

        if not _is_valid_bar(bar):
            self._quality["invalid_bars"] += 1
            return ()

        if bar.volume <= 0:
            self._quality["missing_volume"] += 1

        self._times.append(bar.time)
        self._opens.append(bar.open)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._closes.append(bar.close)
        self._volumes.append(bar.volume)

        index = len(self._times) - 1
        events: list[StructureEvent] = []

        pivot_high_index = index - self._config.pivot_length
        got_pivot_high = self._is_pivot_high(pivot_high_index)
        got_pivot_low = self._is_pivot_low(pivot_high_index)

        if got_pivot_high:
            self._register_high(pivot_high_index)
        if got_pivot_low:
            self._register_low(pivot_high_index)

        suppress_high = self._config.suppress_break_on_new_pivot and got_pivot_high
        suppress_low = self._config.suppress_break_on_new_pivot and got_pivot_low

        if not suppress_high:
            bullish_event = self._check_bullish_break(index)
            if bullish_event is not None:
                events.append(bullish_event)

        if not suppress_low:
            bearish_event = self._check_bearish_break(index)
            if bearish_event is not None:
                events.append(bearish_event)

        if self._config.detect_failed_breakout:
            failed = self._check_failed_breakout(index)
            if failed is not None:
                events.append(failed)

        self._events.extend(events)
        self._refresh_trend()
        self._update_insufficient_flag()
        return tuple(events)

    def analyze(
        self,
        candles: Sequence[CandleBar],
        *,
        symbol: str = "",
        timeframe: str = "1D",
        is_final_candle: bool = True,
    ) -> StructureSnapshot:
        """Chạy engine trên toàn bộ chuỗi nến.

        Args:
            candles: Danh sách nến đã đóng, sắp xếp tăng dần theo thời gian.
            symbol: Mã chứng khoán để gắn vào snapshot.
            timeframe: Khung thời gian.
            is_final_candle: False khi nến cuối cùng còn đang hình thành.

        Returns:
            `StructureSnapshot` sau khi xử lý hết chuỗi. Nến đang hình thành ở cuối
            chuỗi bị loại khỏi tính toán, nên nó không thể tạo pivot hay sự kiện.
        """
        self.reset()
        closed = list(candles)
        if not is_final_candle and closed:
            closed = closed[:-1]
        for bar in closed:
            self.feed(bar)
        return self.snapshot(symbol=symbol, timeframe=timeframe, is_final_candle=is_final_candle)

    def snapshot(
        self,
        *,
        symbol: str = "",
        timeframe: str = "1D",
        is_final_candle: bool = True,
    ) -> StructureSnapshot:
        """Tạo snapshot từ trạng thái hiện tại của engine.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            is_final_candle: False khi nến cuối cùng còn đang hình thành.

        Returns:
            `StructureSnapshot` phản ánh trạng thái đã xử lý.
        """
        last_high = self._last_swing(PivotType.HIGH)
        last_low = self._last_swing(PivotType.LOW)

        return StructureSnapshot(
            symbol=symbol.upper(),
            timeframe=timeframe,
            as_of=self._times[-1] if self._times else None,
            break_bias=self._bias,
            confirmed_trend=self._trend,
            last_swing_high=last_high,
            last_swing_low=last_low,
            protected_high=None if self._high_broken else last_high,
            protected_low=None if self._low_broken else last_low,
            swings=tuple(self._swings),
            events=tuple(self._events),
            latest_event=self._events[-1] if self._events else None,
            engine_version=ENGINE_VERSION,
            config_hash=self._config.config_hash,
            candle_finality="closed" if is_final_candle else "forming",
            data_quality=dict(self._quality),
        )

    def _is_pivot_high(self, index: int) -> bool:
        """Kiểm tra bar tại `index` có phải pivot high đã được xác nhận."""
        length = self._config.pivot_length
        if index < length or index + length >= len(self._highs):
            return False
        candidate = self._highs[index]
        return candidate > max(self._highs[index - length : index]) and candidate > max(
            self._highs[index + 1 : index + length + 1]
        )

    def _is_pivot_low(self, index: int) -> bool:
        """Kiểm tra bar tại `index` có phải pivot low đã được xác nhận."""
        length = self._config.pivot_length
        if index < length or index + length >= len(self._lows):
            return False
        candidate = self._lows[index]
        return candidate < min(self._lows[index - length : index]) and candidate < min(
            self._lows[index + 1 : index + length + 1]
        )

    def _register_high(self, index: int) -> None:
        """Cập nhật swing high mới và nhãn HH/LH/EQH."""
        previous = self._last_high
        price = self._highs[index]
        self._last_high = price
        self._last_high_index = index
        self._high_broken = False

        label: SwingLabel | None = None
        if previous is not None:
            label = self._compare_high(price, previous)
        self._last_label_high = label
        self._swings.append(
            SwingPoint(
                kind=PivotType.HIGH,
                label=label,
                price=price,
                pivot_time=self._times[index],
                confirmed_at=self._times[-1],
                index=index,
            )
        )

    def _register_low(self, index: int) -> None:
        """Cập nhật swing low mới và nhãn HL/LL/EQL."""
        previous = self._last_low
        price = self._lows[index]
        self._last_low = price
        self._last_low_index = index
        self._low_broken = False

        label: SwingLabel | None = None
        if previous is not None:
            label = self._compare_low(price, previous)
        self._last_label_low = label
        self._swings.append(
            SwingPoint(
                kind=PivotType.LOW,
                label=label,
                price=price,
                pivot_time=self._times[index],
                confirmed_at=self._times[-1],
                index=index,
            )
        )

    def _compare_high(self, price: float, previous: float) -> SwingLabel:
        """So sánh hai pivot high liên tiếp để gán nhãn."""
        tolerance = self._config.equal_tolerance_pct
        if (
            tolerance > 0
            and previous != 0
            and abs(price - previous) / abs(previous) * 100 <= tolerance
        ):
            return SwingLabel.EQH
        if price > previous:
            return SwingLabel.HH
        if price < previous:
            return SwingLabel.LH
        # Chế độ Pine: hai đỉnh bằng nhau cho nhãn LH.
        return SwingLabel.LH

    def _compare_low(self, price: float, previous: float) -> SwingLabel:
        """So sánh hai pivot low liên tiếp để gán nhãn."""
        tolerance = self._config.equal_tolerance_pct
        if (
            tolerance > 0
            and previous != 0
            and abs(price - previous) / abs(previous) * 100 <= tolerance
        ):
            return SwingLabel.EQL
        if price > previous:
            return SwingLabel.HL
        if price < previous:
            return SwingLabel.LL
        # Chế độ Pine: hai đáy bằng nhau cho nhãn LL.
        return SwingLabel.LL

    def _break_level_high(self) -> float | None:
        """Mức kháng cự cần vượt để phá cấu trúc tăng."""
        if self._last_high is None:
            return None
        buffer = self._config.breakout_buffer_atr
        if buffer > 0:
            atr = self._atr()
            if atr is not None:
                return self._last_high + buffer * atr
        return self._last_high

    def _break_level_low(self) -> float | None:
        """Mức hỗ trợ cần thủng để phá cấu trúc giảm."""
        if self._last_low is None:
            return None
        buffer = self._config.breakout_buffer_atr
        if buffer > 0:
            atr = self._atr()
            if atr is not None:
                return self._last_low - buffer * atr
        return self._last_low

    def _atr(self) -> float | None:
        """ATR tại nến cuối, có cache theo độ dài chuỗi."""
        if self._atr_cache is not None and self._atr_cache[0] == len(self._times):
            return self._atr_cache[1]
        period = self._config.atr_period
        if len(self._times) < period + 1:
            self._atr_cache = (len(self._times), None)
            return None
        values = talib.ATR(
            np.asarray(self._highs, dtype=np.float64),
            np.asarray(self._lows, dtype=np.float64),
            np.asarray(self._closes, dtype=np.float64),
            timeperiod=period,
        )
        last = float(values[-1])
        result = None if np.isnan(last) else last
        self._atr_cache = (len(self._times), result)
        return result

    def _is_displacement(self, index: int) -> bool:
        """Kiểm tra thân nến tại `index` có vượt ATR × hệ số."""
        atr = self._atr()
        if atr is None or atr == 0:
            return False
        body = abs(self._closes[index] - self._opens[index])
        return body > atr * self._config.displacement_atr_multiple

    def _check_bullish_break(self, index: int) -> StructureEvent | None:
        """Kiểm tra phá kháng cự tại nến `index` và cập nhật bias."""
        if self._high_broken or self._last_high is None or self._last_high_index is None:
            return None

        level = self._break_level_high()
        if level is None:
            return None

        if self._config.break_confirmation == "wick":
            triggered = self._highs[index] > level
            if self._config.require_crossing and index > 0:
                triggered = triggered and self._highs[index - 1] <= level
            observed = self._highs[index]
        else:
            triggered = self._closes[index] > level
            if self._config.require_crossing and index > 0:
                triggered = triggered and self._closes[index - 1] <= level
            observed = self._closes[index]

        if not triggered:
            return None

        is_change = self._bias is BreakBias.BEARISH
        bias_before = self._bias
        self._bias = BreakBias.BULLISH
        self._high_broken = True

        displacement = self._is_displacement(index)
        if is_change:
            event_type = (
                StructureEventType.MSS_BULLISH
                if self._config.emit_mss and displacement
                else StructureEventType.CHOCH_BULLISH
            )
        else:
            event_type = StructureEventType.BOS_BULLISH

        event = StructureEvent(
            event_type=event_type,
            pivot_time=self._times[self._last_high_index],
            confirmed_at=self._times[self._last_high_index + self._config.pivot_length],
            broken_at=self._times[index],
            price=self._last_high,
            break_level=level,
            observed_price=observed,
            bias_before=bias_before,
            bias_after=self._bias,
            is_displacement=displacement,
        )
        if self._config.detect_failed_breakout:
            self._pending_break = (event_type, level, index)
        return event

    def _check_bearish_break(self, index: int) -> StructureEvent | None:
        """Kiểm tra thủng hỗ trợ tại nến `index` và cập nhật bias."""
        if self._low_broken or self._last_low is None or self._last_low_index is None:
            return None

        level = self._break_level_low()
        if level is None:
            return None

        if self._config.break_confirmation == "wick":
            triggered = self._lows[index] < level
            if self._config.require_crossing and index > 0:
                triggered = triggered and self._lows[index - 1] >= level
            observed = self._lows[index]
        else:
            triggered = self._closes[index] < level
            if self._config.require_crossing and index > 0:
                triggered = triggered and self._closes[index - 1] >= level
            observed = self._closes[index]

        if not triggered:
            return None

        is_change = self._bias is BreakBias.BULLISH
        bias_before = self._bias
        self._bias = BreakBias.BEARISH
        self._low_broken = True

        displacement = self._is_displacement(index)
        if is_change:
            event_type = (
                StructureEventType.MSS_BEARISH
                if self._config.emit_mss and displacement
                else StructureEventType.CHOCH_BEARISH
            )
        else:
            event_type = StructureEventType.BOS_BEARISH

        event = StructureEvent(
            event_type=event_type,
            pivot_time=self._times[self._last_low_index],
            confirmed_at=self._times[self._last_low_index + self._config.pivot_length],
            broken_at=self._times[index],
            price=self._last_low,
            break_level=level,
            observed_price=observed,
            bias_before=bias_before,
            bias_after=self._bias,
            is_displacement=displacement,
        )
        if self._config.detect_failed_breakout:
            self._pending_break = (event_type, level, index)
        return event

    def _check_failed_breakout(self, index: int) -> StructureEvent | None:
        """Phát hiện nến đóng quay lại vùng vừa phá trong cửa sổ cho phép."""
        if self._pending_break is None:
            return None

        event_type, level, break_index = self._pending_break
        if index - break_index > self._config.failed_breakout_window:
            self._pending_break = None
            return None

        close = self._closes[index]
        is_bullish = event_type in {
            StructureEventType.BOS_BULLISH,
            StructureEventType.CHOCH_BULLISH,
            StructureEventType.MSS_BULLISH,
        }

        if is_bullish and close < level:
            self._pending_break = None
            return StructureEvent(
                event_type=StructureEventType.FAILED_BREAKOUT_BULLISH,
                pivot_time=self._times[break_index],
                confirmed_at=self._times[break_index],
                broken_at=self._times[index],
                price=level,
                break_level=level,
                observed_price=close,
                bias_before=BreakBias.BULLISH,
                bias_after=self._bias,
            )

        if not is_bullish and close > level:
            self._pending_break = None
            return StructureEvent(
                event_type=StructureEventType.FAILED_BREAKOUT_BEARISH,
                pivot_time=self._times[break_index],
                confirmed_at=self._times[break_index],
                broken_at=self._times[index],
                price=level,
                break_level=level,
                observed_price=close,
                bias_before=BreakBias.BEARISH,
                bias_after=self._bias,
            )

        return None

    def _refresh_trend(self) -> None:
        """Cập nhật `confirmed_trend` từ nhãn swing đã xác nhận."""
        high_label = self._last_label_high
        low_label = self._last_label_low
        bullish_high = high_label in {SwingLabel.HH, SwingLabel.EQH}
        bearish_high = high_label in {SwingLabel.LH, SwingLabel.EQH}
        bullish_low = low_label in {SwingLabel.HL, SwingLabel.EQL}
        bearish_low = low_label in {SwingLabel.LL, SwingLabel.EQL}

        if bullish_high and bullish_low:
            self._trend = ConfirmedTrend.UPTREND
        elif bearish_high and bearish_low:
            self._trend = ConfirmedTrend.DOWNTREND
        elif high_label is not None and low_label is not None:
            # Hai nhãn còn mâu thuẫn (ví dụ HH đi cùng LL): cấu trúc đang chuyển pha.
            self._trend = ConfirmedTrend.TRANSITION
        else:
            self._trend = ConfirmedTrend.SIDEWAYS

        # CHoCH chỉ là dấu hiệu chuyển pha: nếu nó đi ngược cấu trúc swing đã xác
        # nhận thì trạng thái là TRANSITION, không phải đảo chiều đã xác nhận.
        if self._events:
            latest = self._events[-1].event_type
            reversal_against_uptrend = latest in {
                StructureEventType.CHOCH_BEARISH,
                StructureEventType.MSS_BEARISH,
            }
            reversal_against_downtrend = latest in {
                StructureEventType.CHOCH_BULLISH,
                StructureEventType.MSS_BULLISH,
            }
            if (self._trend is ConfirmedTrend.UPTREND and reversal_against_uptrend) or (
                self._trend is ConfirmedTrend.DOWNTREND and reversal_against_downtrend
            ):
                self._trend = ConfirmedTrend.TRANSITION

    def _update_insufficient_flag(self) -> None:
        """Đánh dấu khi chuỗi chưa đủ dài để xác nhận pivot."""
        required = self._config.pivot_length * 2 + 1
        self._quality["insufficient_history"] = len(self._times) < required

    def _last_swing(self, kind: PivotType) -> SwingPoint | None:
        """Trả swing gần nhất theo loại pivot."""
        for swing in reversed(self._swings):
            if swing.kind is kind:
                return swing
        return None


def analyze_structure(
    candles: Sequence[CandleBar],
    *,
    symbol: str = "",
    timeframe: str = "1D",
    config: MarketStructureConfig | None = None,
    is_final_candle: bool = True,
) -> StructureSnapshot:
    """Chạy engine cấu trúc trên một chuỗi nến.

    Args:
        candles: Danh sách nến đã đóng, tăng dần theo thời gian.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        config: Cấu hình engine.
        is_final_candle: False khi nến cuối cùng còn đang hình thành.

    Returns:
        `StructureSnapshot` của chuỗi đầu vào.
    """
    engine = MarketStructureEngine(config)
    return engine.analyze(
        candles, symbol=symbol, timeframe=timeframe, is_final_candle=is_final_candle
    )
