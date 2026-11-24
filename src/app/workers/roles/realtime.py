"""Realtime role: nhận tick FCData và kích hoạt job khi phiên đóng.

Role này **không** ghi nến minute vào bảng ngày. Nến đang hình thành chỉ được
cache ngắn hạn để hiển thị; dữ liệu confirmed được lấy từ `ingest-close` sau khi
phiên đóng, rồi `recompute-indicators` chạy trên nến đã đóng.

Scheduler vẫn là lưới an toàn: nếu trigger realtime lỗi hoặc process không chạy,
các job 15:05/15:20 sẽ bổ sung dữ liệu thiếu. Engine idempotency bảo đảm hai
nguồn kích hoạt không chạy trùng trong cùng ngày.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.fcdata.client import FcDataClient
from app.fcdata.stream import FcDataStream, QuoteTick
from app.indicators.market_structure import CandleBar
from app.repositories.market_repository import MarketRepository
from app.services.screener_realtime_service import ScreenerRealtimeProducer
from app.workers.engine import WorkerEngine
from app.workers.heartbeat import Heartbeat, heartbeat_path
from app.workers.registry import Trigger
from app.workers.schedule import parse_holidays

logger = get_logger(__name__)

_FORMING_KEY_PREFIX = "forming"


@dataclass(slots=True)
class FormingCandle:
    """Nến đang hình thành từ các tick realtime.

    Attributes:
        symbol: Mã chứng khoán.
        start: Thời điểm mở khung thời gian.
        open: Giá tick đầu tiên.
        high: Giá cao nhất đã thấy.
        low: Giá thấp nhất đã thấy.
        close: Giá tick gần nhất.
        volume: Khối lượng cộng dồn.
    """

    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class FormingCandleAggregator:
    """Gom tick thành nến đang hình thành và nhả ra nến đã đóng."""

    def __init__(self, *, timeframe_minutes: int = 1, market_tz: ZoneInfo) -> None:
        """Khởi tạo aggregator.

        Args:
            timeframe_minutes: Độ dài một khung thời gian, tính bằng phút.
            market_tz: Múi giờ thị trường để cắt khung.
        """
        self._minutes = max(1, timeframe_minutes)
        self._tz = market_tz
        self._forming: dict[str, FormingCandle] = {}

    @property
    def timeframe_minutes(self) -> int:
        """Độ dài khung gom tick, tính bằng phút."""
        return self._minutes

    def bucket_start(self, moment: datetime) -> datetime:
        """Thời điểm mở khung thời gian chứa `moment`.

        Args:
            moment: Thời điểm cần cắt khung.

        Returns:
            Đầu khung thời gian theo múi giờ thị trường.
        """
        local = moment.astimezone(self._tz)
        floored_minute = local.minute - (local.minute % self._minutes)
        return local.replace(minute=floored_minute, second=0, microsecond=0)

    def on_tick(self, tick: QuoteTick, *, now: datetime | None = None) -> list[CandleBar]:
        """Nạp một tick và trả về các nến vừa đóng.

        Args:
            tick: Tick đã chuẩn hoá.
            now: Thời điểm hiện tại, dùng để test.

        Returns:
            Danh sách nến đã đóng; rỗng khi tick vẫn thuộc nến đang hình thành.
        """
        if tick.price is None:
            return []

        moment = now or datetime.now(tz=self._tz)
        bucket = self.bucket_start(moment)
        closed: list[CandleBar] = []

        current = self._forming.get(tick.symbol)
        if current is not None and current.start != bucket:
            closed.append(self._to_bar(current))
            self._forming.pop(tick.symbol, None)
            current = None

        if current is None:
            self._forming[tick.symbol] = FormingCandle(
                symbol=tick.symbol,
                start=bucket,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=float(tick.volume or 0),
            )
        else:
            current.high = max(current.high, tick.price)
            current.low = min(current.low, tick.price)
            current.close = tick.price
            current.volume += float(tick.volume or 0)

        return closed

    def forming_snapshot(self) -> list[FormingCandle]:
        """Ảnh chụp các nến đang hình thành."""
        return list(self._forming.values())

    @staticmethod
    def _to_bar(candle: FormingCandle) -> CandleBar:
        """Chuyển nến đang hình thành thành nến đã đóng."""
        return CandleBar(
            time=candle.start,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )


TickHandler = Callable[[QuoteTick], Awaitable[None]]
StreamFactory = Callable[[Settings, TickHandler, list[str], list[str]], FcDataStream]


def parse_close_time(value: str) -> time:
    """Đọc giờ đóng phiên dạng `HH:MM`.

    Args:
        value: Chuỗi cấu hình.

    Returns:
        `datetime.time` tương ứng.

    Raises:
        ValueError: Khi chuỗi sai định dạng hoặc ngoài 0..23:00..59.
    """
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, ValueError) as exc:
        raise ValueError("WORKER_SESSION_CLOSE_TIME phải có dạng HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("WORKER_SESSION_CLOSE_TIME nằm ngoài 0..23:00..59")
    return time(hour, minute)


def is_session_close_due(
    now: datetime,
    close_time: time,
    *,
    last_trigger: date | None,
    holidays: frozenset[date],
) -> bool:
    """Phiên đã đóng và chưa kích hoạt job trong ngày hay chưa.

    Args:
        now: Thời điểm hiện tại có timezone.
        close_time: Giờ đóng phiên.
        last_trigger: Ngày kích hoạt gần nhất.
        holidays: Tập ngày lễ cần bỏ qua.

    Returns:
        True khi cần chạy chuỗi job đóng phiên.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_session_close_due yêu cầu datetime có timezone")
    today = now.date()
    if today.weekday() >= 5 or today in holidays:
        return False
    if last_trigger == today:
        return False
    return now.timetz().replace(tzinfo=None) >= close_time


class RealtimeRole:
    """Stream tick + trigger job đóng phiên, không ghi nến minute vào DB."""

    def __init__(
        self,
        settings: Settings,
        engine: WorkerEngine,
        *,
        symbols: list[str],
        indices: list[str],
        timeframe_minutes: int = 1,
        trigger_on_session_close: bool = True,
        heartbeat: Heartbeat | None = None,
        aggregator: FormingCandleAggregator | None = None,
        redis: Redis | None = None,
        clock: Callable[[], datetime] | None = None,
        stream_factory: StreamFactory | None = None,
        holidays: frozenset[date] | None = None,
    ) -> None:
        """Khởi tạo role.

        Args:
            settings: Cấu hình ứng dụng.
            engine: Executor dùng chung.
            symbols: Mã cổ phiếu cần subscribe.
            indices: Mã chỉ số cần subscribe.
            timeframe_minutes: Khung gom tick cho snapshot provisional.
            trigger_on_session_close: Bật trigger ingest/recompute sau giờ đóng.
            heartbeat: Bộ ghi heartbeat.
            aggregator: Aggregator tuỳ chọn cho test.
            redis: Redis client tuỳ chọn; mặc định lấy client chung.
            clock: Hàm thời gian, inject cho test.
            stream_factory: Factory stream, inject cho test.
            holidays: Tập ngày lễ; mặc định parse từ cấu hình.
        """
        self._settings = settings
        self._engine = engine
        self._symbols = [item.strip().upper() for item in symbols if item.strip()]
        self._indices = [item.strip().upper() for item in indices if item.strip()]
        self._trigger_on_session_close = trigger_on_session_close
        self._aggregator = aggregator or FormingCandleAggregator(
            timeframe_minutes=timeframe_minutes, market_tz=settings.market_tz
        )
        self._redis = redis if redis is not None else get_redis_client()
        self._clock = clock or (lambda: datetime.now(tz=settings.market_tz))
        self._close_time = parse_close_time(settings.worker_session_close_time)
        self._holidays = (
            holidays if holidays is not None else parse_holidays(settings.scheduler_holidays)
        )
        self._heartbeat = heartbeat or Heartbeat(
            heartbeat_path(settings, "realtime"),
            "realtime",
            settings.worker_heartbeat_seconds,
        )
        self._stream_factory = stream_factory or (
            lambda config, on_tick, symbols, indices: FcDataStream(
                config, on_tick=on_tick, symbols=symbols, indices=indices
            )
        )
        self._last_session_date: date | None = None
        self._trigger_lock = asyncio.Lock()
        self._ticks = 0
        self._closed_bars = 0
        self._last_tick_at: datetime | None = None
        self._last_trigger_outcome: dict[str, object] = {}
        self._screener_producer: ScreenerRealtimeProducer | None = None
        self._fcdata_client: FcDataClient | None = None
        self._screener_symbols: list[str] = []

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Chạy stream và monitor đóng phiên cho tới khi dừng.

        Args:
            stop_event: Sự kiện dừng.
        """
        stop = stop_event or asyncio.Event()
        producer_task: asyncio.Task[None] | None = None
        stream_symbols = list(self._symbols)
        if self._settings.screener_realtime_enabled and self._redis is not None:
            self._screener_symbols = await self._load_screener_universe()
            stream_symbols = sorted(set(self._symbols) | set(self._screener_symbols))
            self._fcdata_client = FcDataClient(self._settings)
            self._screener_producer = ScreenerRealtimeProducer(
                self._settings,
                symbols=self._screener_symbols,
                redis=self._redis,
                fcdata=self._fcdata_client,
                history_loader=self._load_history,
            )
            producer_task = asyncio.create_task(
                self._screener_producer.run(stop), name="screener-realtime"
            )
        stream = self._stream_factory(
            self._settings, self.on_tick, stream_symbols, self._indices
        )
        stream_task = asyncio.create_task(stream.run(stop), name="realtime-stream")
        monitor_task: asyncio.Task[None] | None = None
        if self._trigger_on_session_close and self._symbols:
            monitor_task = asyncio.create_task(
                self._session_monitor(stop), name="realtime-session-monitor"
            )
        heartbeat_task = asyncio.create_task(
            self._heartbeat.run(stop, status_provider=self._health_detail),
            name="realtime-heartbeat",
        )
        stop_wait = asyncio.create_task(stop.wait(), name="realtime-stop")
        logger.info(
            "realtime_started",
            extra={
                "symbols": self._symbols,
                "indices": self._indices,
                "screener_symbols": len(self._screener_symbols),
                "screener_realtime": self._screener_producer is not None,
                "trigger_on_session_close": self._trigger_on_session_close,
            },
        )
        try:
            done, _pending = await asyncio.wait(
                {stream_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if stream_task in done:
                # Stream chỉ kết thúc khi stop được set hoặc lỗi không thể phục hồi.
                stream_task.result()
            else:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(stream_task),
                        timeout=self._settings.worker_shutdown_grace_seconds,
                    )
                except TimeoutError:
                    stream_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stream_task
        finally:
            stop_wait.cancel()
            if monitor_task is not None:
                monitor_task.cancel()
            heartbeat_task.cancel()
            if producer_task is not None:
                producer_task.cancel()
            for task in (stop_wait, monitor_task, heartbeat_task, producer_task):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if self._fcdata_client is not None:
                with contextlib.suppress(Exception):
                    await self._fcdata_client.close()
            self._heartbeat.write(status="stopped", detail=self._health_detail())
            logger.info("realtime_stopped", extra=self._health_detail())

    async def on_tick(self, tick: QuoteTick) -> None:
        """Xử lý một tick: cập nhật snapshot provisional và thống kê.

        Args:
            tick: Tick đã chuẩn hoá.
        """
        closed = self._aggregator.on_tick(tick)
        self._ticks += 1
        self._closed_bars += len(closed)
        self._last_tick_at = self._clock()
        await self._cache_forming(tick.symbol)
        if self._screener_producer is not None:
            await self._screener_producer.on_tick(tick)

    async def _cache_forming(self, symbol: str) -> None:
        """Ghi nến đang hình thành vào Redis với TTL ngắn.

        Args:
            symbol: Mã vừa có tick.
        """
        if self._redis is None:
            return
        for candle in self._aggregator.forming_snapshot():
            if candle.symbol != symbol:
                continue
            payload = {
                "symbol": candle.symbol,
                "start": candle.start.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "state": "FORMING",
            }
            key = f"{_FORMING_KEY_PREFIX}:{candle.symbol}:{self._aggregator.timeframe_minutes}m"
            try:
                await self._redis.set(
                    key, json.dumps(payload), ex=self._settings.redis_quote_ttl_seconds
                )
            except Exception as exc:  # noqa: BLE001 - cache lỗi không được làm chết stream
                logger.warning(
                    "forming_cache_failed",
                    extra={"symbol": symbol, "error_type": type(exc).__name__},
                )

    async def _session_monitor(self, stop: asyncio.Event) -> None:
        """Kiểm tra giờ đóng phiên theo chu kỳ, kể cả khi không có tick."""
        while not stop.is_set():
            await self.trigger_session_close_if_due()
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._settings.worker_session_check_seconds
                )
            except TimeoutError:
                continue

    async def trigger_session_close_if_due(self) -> None:
        """Chạy chuỗi ingest/recompute một lần cho mỗi ngày giao dịch."""
        async with self._trigger_lock:
            now = self._clock()
            if not is_session_close_due(
                now,
                self._close_time,
                last_trigger=self._last_session_date,
                holidays=self._holidays,
            ):
                return
            # Đánh dấu trước khi chạy để monitor không trigger chồng khi job lâu.
            self._last_session_date = now.date()
            await self._run_session_jobs()

    async def _run_session_jobs(self) -> None:
        """Gọi ingest-close rồi recompute-indicators cho watchlist."""
        jobs: tuple[tuple[str, dict[str, object], Trigger], ...] = (
            (
                "ingest-close",
                {
                    "symbols": self._symbols,
                    "days": self._settings.ingest_incremental_days,
                    "timeframe": "1D",
                },
                "realtime",
            ),
            (
                "recompute-indicators",
                {"symbols": self._symbols, "timeframe": "1D"},
                "realtime",
            ),
        )
        for name, params, trigger in jobs:
            try:
                outcome = await self._engine.execute(name, params, trigger=trigger)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - scheduler sẽ bổ sung sau
                logger.warning(
                    "realtime_job_failed",
                    extra={"job": name, "error_type": type(exc).__name__},
                )
                continue
            self._last_trigger_outcome = {
                "last_job": name,
                "last_status": str(outcome.status),
                "last_finished_at": self._clock().isoformat(),
            }
            logger.info(
                "realtime_job_finished",
                extra={
                    "job": name,
                    "status": str(outcome.status),
                    "attempts": outcome.attempts,
                    "elapsed_ms": outcome.elapsed_ms,
                },
            )

    async def _load_screener_universe(self) -> list[str]:
        """Universe cho screener realtime theo cấu hình."""
        if self._settings.screener_realtime_mode == "watchlist":
            return list(self._symbols)
        async with session_scope() as session:
            rows, _total = await MarketRepository(session).list_symbols(
                active_only=True,
                limit=self._settings.screener_realtime_max_symbols,
            )
        return [row.symbol for row in rows]

    async def _load_history(
        self, symbols: Sequence[str], limit: int, end: datetime
    ) -> dict[str, list[CandleBar]]:
        """Đọc nến đã đóng cho producer, một query bulk."""
        async with session_scope() as session:
            return await MarketRepository(session).recent_candles_by_symbol(
                list(symbols), limit=limit, end=end
            )

    def _health_detail(self) -> dict[str, object]:
        """Detail heartbeat của role."""
        return {
            "symbols": len(self._symbols),
            "indices": len(self._indices),
            "screener_symbols": len(self._screener_symbols),
            "screener_realtime": self._screener_producer is not None,
            "ticks": self._ticks,
            "closed_bars": self._closed_bars,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "last_session_date": (
                self._last_session_date.isoformat() if self._last_session_date else None
            ),
            **self._last_trigger_outcome,
        }
