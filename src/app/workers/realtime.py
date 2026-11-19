"""Worker realtime: nhận tick và quản lý nến đang hình thành.

Quy tắc bắt buộc: worker **không** ghi đè nến đã đóng. Tick được gom thành nến
đang hình thành, giữ ở Redis hoặc trong bộ nhớ; chỉ khi nến thực sự đóng (sang
khung thời gian mới) mới upsert vào PostgreSQL.

Chạy độc lập với API::

    uv run python -m app.workers.realtime --symbols FPT,HPG --indices VNINDEX
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings
from app.core.database import dispose_engine, session_scope
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis_client, get_redis_client
from app.fcdata.mapper import CandleRecord
from app.fcdata.stream import FcDataStream, QuoteTick
from app.indicators.market_structure import CandleBar
from app.indicators.native import configure_unstable_periods
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import IndicatorService

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


class RealtimeWorker:
    """Ghép stream tick với aggregator, Redis cache và PostgreSQL."""

    def __init__(
        self,
        settings: Settings,
        *,
        symbols: list[str],
        indices: list[str],
        timeframe_minutes: int = 1,
        recompute_on_close: bool = False,
    ) -> None:
        """Khởi tạo worker.

        Args:
            settings: Cấu hình ứng dụng.
            symbols: Danh sách mã cổ phiếu cần đăng ký.
            indices: Danh sách mã chỉ số cần đăng ký.
            timeframe_minutes: Độ dài khung thời gian gom tick.
            recompute_on_close: Tính lại chỉ báo ngay khi nến đóng.
        """
        self._settings = settings
        self._symbols = symbols
        self._indices = indices
        self._timeframe_minutes = timeframe_minutes
        self._recompute_on_close = recompute_on_close
        self._aggregator = FormingCandleAggregator(
            timeframe_minutes=timeframe_minutes, market_tz=settings.market_tz
        )
        self._redis = get_redis_client()

    async def handle_tick(self, tick: QuoteTick) -> None:
        """Xử lý một tick: cache nến đang hình thành và lưu nến đã đóng."""
        closed = self._aggregator.on_tick(tick)
        await self._cache_forming(tick.symbol)
        for bar in closed:
            await self._persist_closed_bar(tick.symbol, bar)

    async def _cache_forming(self, symbol: str) -> None:
        """Ghi nến đang hình thành vào Redis với TTL ngắn."""
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
            key = f"{_FORMING_KEY_PREFIX}:{candle.symbol}:{self._timeframe_minutes}m"
            await self._redis.set(
                key, json.dumps(payload), ex=self._settings.redis_quote_ttl_seconds
            )

    async def _persist_closed_bar(self, symbol: str, bar: CandleBar) -> None:
        """Upsert nến đã đóng và tuỳ chọn tính lại chỉ báo."""
        async with session_scope() as session:
            repository = MarketRepository(session)
            await repository.ensure_symbol(symbol)
            await repository.upsert_candles(
                [
                    CandleRecord(
                        symbol=symbol,
                        timeframe="1D",
                        open_time=bar.time,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=int(bar.volume),
                    )
                ]
            )
            logger.info(
                "closed_bar_persisted",
                extra={"symbol": symbol, "start": bar.time.isoformat(), "close": bar.close},
            )

            if self._recompute_on_close:
                service = IndicatorService(
                    settings=self._settings,
                    market_repository=repository,
                    indicator_repository=IndicatorRepository(session),
                )
                try:
                    await service.recompute(symbol, "1D")
                except Exception as exc:  # noqa: BLE001 - worker không được chết vì một mã lỗi
                    logger.warning(
                        "recompute_after_close_failed",
                        extra={"symbol": symbol, "error_type": type(exc).__name__},
                    )

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Chạy stream cho tới khi bị dừng.

        Args:
            stop_event: Sự kiện dừng.
        """
        stream = FcDataStream(
            self._settings,
            on_tick=self.handle_tick,
            symbols=self._symbols,
            indices=self._indices,
        )
        await stream.run(stop_event)


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh của worker."""
    parser = argparse.ArgumentParser(description="Worker realtime cho FCData Gateway.")
    parser.add_argument(
        "--symbols", default="", help="Danh sách mã cổ phiếu, phân tách bằng dấu phẩy."
    )
    parser.add_argument(
        "--indices", default="", help="Danh sách mã chỉ số, phân tách bằng dấu phẩy."
    )
    parser.add_argument(
        "--timeframe-minutes",
        type=int,
        default=1,
        help="Độ dài khung thời gian gom tick, tính bằng phút.",
    )
    parser.add_argument(
        "--recompute-on-close",
        action="store_true",
        help="Tính lại chỉ báo ngay khi nến đóng.",
    )
    return parser.parse_args()


async def main() -> None:
    """Entrypoint của worker."""
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_unstable_periods(settings.indicator_unstable_periods)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    indices = [item.strip().upper() for item in args.indices.split(",") if item.strip()]
    worker = RealtimeWorker(
        settings,
        symbols=symbols,
        indices=indices,
        timeframe_minutes=args.timeframe_minutes,
        recompute_on_close=args.recompute_on_close,
    )

    try:
        await worker.run()
    finally:
        await close_redis_client()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
