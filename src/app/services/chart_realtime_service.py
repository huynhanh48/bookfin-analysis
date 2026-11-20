"""Demand-driven FCData snapshots feeding the transport-independent realtime hub.

Ticks invalidate snapshots; periodic reconciliation also works when SSI is not ready.
Provider OHLCV replaces snapshots atomically, never sums cumulative tick volume.
No provisional data is persisted or passed to confirmed indicator calculation.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from app.core.config import Settings
from app.core.logging import get_logger
from app.fcdata.mapper import map_candles
from app.fcdata.stream import FcDataStream, QuoteTick
from app.schemas.realtime import RealtimeEvent
from app.services.realtime_indicators import (
    ClosedCandleCache,
    OhlcvRow,
    compute_realtime_indicators,
)
from app.services.realtime_service import RealtimeService

logger = get_logger(__name__)


class CandleSnapshotProvider(Protocol):
    """Provider boundary for authoritative daily session snapshots."""

    async def fetch_daily_ohlc(self, symbol: str, *, start: str, end: str) -> dict: ...


class ChartRealtimeService:
    """Share one upstream stream and bounded snapshot requests across local subscribers."""

    def __init__(
        self,
        settings: Settings,
        hub: RealtimeService,
        provider: CandleSnapshotProvider,
        closed_candles: ClosedCandleCache | None = None,
    ):
        self.settings = settings
        self.hub = hub
        self.provider = provider
        # Không có cache (ví dụ trong unit test) thì chỉ publish OHLC, bỏ qua chỉ báo.
        self.closed_candles = closed_candles
        self._dirty: set[str] = set()
        self._slots = asyncio.Semaphore(settings.chart_realtime_concurrency)

    async def on_tick(self, tick: QuoteTick) -> None:
        """Coalesce bursts rather than making a REST call for every trade."""
        self._dirty.add(tick.symbol)

    async def refresh(self, symbol: str, channels: set[str]) -> None:
        """Publish current day/week snapshots with the same timezone convention as history."""
        now = datetime.now(self.settings.market_tz)
        monday = now.date() - timedelta(days=now.weekday())
        start = monday if f"market:{symbol}:1W" in channels else now.date()
        async with self._slots:
            payload = await self.provider.fetch_daily_ohlc(
                symbol, start=start.strftime("%d/%m/%Y"), end=now.strftime("%d/%m/%Y")
            )
        batch = map_candles(
            payload.get("data") or [],
            symbol=symbol,
            timeframe="1D",
            price_basis="RAW",
            tz=self.settings.market_tz,
        )
        rows = sorted(
            (
                row
                for row in batch.candles
                if start <= row.open_time.astimezone(self.settings.market_tz).date() <= now.date()
            ),
            key=lambda row: row.open_time,
        )
        for frame in ("1D", "1W"):
            channel = f"market:{symbol}:{frame}"
            selected = (
                rows
                if frame == "1W"
                else [
                    r
                    for r in rows
                    if r.open_time.astimezone(self.settings.market_tz).date() == now.date()
                ]
            )
            if channel not in channels or not selected:
                continue
            stamp = (
                selected[0].open_time
                if frame == "1D"
                else datetime.combine(monday, time(), self.settings.market_tz)
            )
            bar_open = selected[0].open
            bar_high = max(r.high for r in selected)
            bar_low = min(r.low for r in selected)
            bar_close = selected[-1].close
            bar_volume = sum(r.volume for r in selected)
            event_payload: dict = {
                "symbol": symbol,
                "timeframe": frame,
                "state": "PROVISIONAL",
                "source": "fcdata:DailyOhlc",
                "price_basis": "RAW",
                "timestamp": int(stamp.timestamp() * 1000),
                "open": bar_open,
                "high": bar_high,
                "low": bar_low,
                "close": bar_close,
                "volume": bar_volume,
            }
            if frame == "1D":
                state, indicators, last_confirmed_at = await self._provisional_state(
                    symbol,
                    now.date(),
                    OhlcvRow(
                        open=float(bar_open),
                        high=float(bar_high),
                        low=float(bar_low),
                        close=float(bar_close),
                        volume=float(bar_volume),
                        day=now.date(),
                    ),
                )
                # Khi DB đã có nến hôm nay, bar này là nến đã đóng.
                payload["state"] = state
                payload["is_final"] = state == "CONFIRMED"
                payload["as_of"] = datetime.now(UTC).isoformat()
                if last_confirmed_at is not None:
                    payload["last_confirmed_at"] = int(last_confirmed_at.timestamp() * 1000)
                if indicators:
                    payload["indicators"] = indicators
            await self.hub.publish(
                RealtimeEvent(type="candle", channel=channel, data=event_payload)
            )

    async def _provisional_state(
        self, symbol: str, trading_day, bar: OhlcvRow
    ) -> tuple[str, dict[str, dict[str, float | None]], datetime | None]:
        """Xác định trạng thái bar và chỉ báo tương ứng.

        Returns:
            Tuple (state, indicators, last_confirmed_at). `state` là CONFIRMED khi
            PostgreSQL đã có nến của ngày giao dịch, ngược lại là PROVISIONAL.
        """
        if self.closed_candles is None:
            return "PROVISIONAL", {}, None
        closed = await self.closed_candles.get(symbol)
        if not closed:
            return "PROVISIONAL", {}, None

        if closed[-1].day == trading_day:
            # Ingestion đã ghi nến hôm nay: đây là bar đã đóng, không ghép thêm.
            return "CONFIRMED", compute_realtime_indicators(list(closed)), closed[-1].opened_at

        rows = [*closed, bar]
        return "PROVISIONAL", compute_realtime_indicators(rows), closed[-1].opened_at

    async def run(self) -> None:
        """Reconcile subscriptions, reconnect upstream, and resnapshot after client reconnects."""
        stream_task: asyncio.Task | None = None
        symbols: set[str] = set()
        last: dict[str, float] = {}
        previous_channels: set[str] = set()
        loop = asyncio.get_running_loop()
        try:
            while True:
                channels = {
                    channel
                    for c in self.hub.connections.values()
                    for channel in c.channels
                    if channel.endswith((":1D", ":1W")) and channel.startswith("market:")
                }
                desired = {c.split(":")[1] for c in channels}
                if desired != symbols:
                    if stream_task:
                        stream_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await stream_task
                    symbols = desired
                    self._dirty.intersection_update(symbols)
                    last = {s: t for s, t in last.items() if s in symbols}
                    stream_task = (
                        asyncio.create_task(
                            FcDataStream(
                                self.settings,
                                on_tick=self.on_tick,
                                symbols=sorted(symbols),
                            ).run()
                        )
                        if symbols
                        else None
                    )
                self._dirty.update(c.split(":")[1] for c in channels - previous_channels)
                previous_channels = channels
                tick_seconds = (
                    self.settings.chart_realtime_single_symbol_seconds
                    if len(symbols) <= 1
                    else self.settings.chart_realtime_tick_seconds
                )
                due = [
                    s
                    for s in symbols
                    if loop.time() - last.get(s, 0)
                    >= (
                        tick_seconds
                        if s in self._dirty
                        else self.settings.chart_realtime_refresh_seconds
                    )
                ]

                async def update(
                    symbol: str, active_channels: set[str], refresh_times: dict[str, float]
                ) -> None:
                    self._dirty.discard(symbol)
                    refresh_times[symbol] = loop.time()
                    try:
                        await self.refresh(symbol, active_channels)
                    except Exception as exc:
                        logger.warning(
                            "chart_snapshot_failed",
                            extra={"symbol": symbol, "error_type": type(exc).__name__},
                        )
                        await self.hub.publish(
                            RealtimeEvent(
                                type="status",
                                channel=f"market:{symbol}:1D",
                                data={"code": "snapshot_unavailable"},
                            )
                        )

                await asyncio.gather(*(update(s, channels, last) for s in due))
                # Nhịp quét ngắn để subscription mới được phục vụ gần như ngay.
                await asyncio.sleep(0.2)
        finally:
            if stream_task:
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
