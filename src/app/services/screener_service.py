"""Nghiệp vụ Stock Screener: historical scan và realtime snapshot.

Service điều phối repository/FCData/Redis; logic lọc nằm trong `ScreenerEngine`
để unit test không cần FastAPI hay database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.fcdata.mapper import QuoteRecord, format_vn_date, map_quote_row
from app.indicators.market_structure import CandleBar
from app.repositories.market_repository import MarketRepository
from app.schemas.screener import (
    DivergenceCondition,
    ScreenerConditions,
    ScreenerResultsResponse,
    ScreenerRow,
    ScreenerScanRequest,
    ScreenerScanResponse,
    ScreenerStatus,
)
from app.services.screener_engine import (
    ScreenerEngine,
    SymbolSnapshot,
    extract_divergence_events,
)

logger = get_logger(__name__)

_RESULTS_PREFIX = "screener:results:"
_ACTIVE_PROFILE_KEY = "screener:active_profile"


class ScreenerService:
    """Quét chiến lược lọc dòng tiền AFL trên PostgreSQL và FCData."""

    def __init__(
        self,
        *,
        settings: Settings,
        market_repository: MarketRepository,
        fcdata: FcDataClient,
        redis: Redis | None = None,
    ) -> None:
        """Khởi tạo service.

        Args:
            settings: Cấu hình ứng dụng.
            market_repository: Repository đọc nến đã đóng.
            fcdata: Client lấy batch quote realtime.
            redis: Redis client để lưu snapshot realtime.
        """
        self._settings = settings
        self._repository = market_repository
        self._fcdata = fcdata
        self._redis = redis
        self._engine = ScreenerEngine()

    @staticmethod
    def default_conditions() -> ScreenerConditions:
        """Cấu hình mặc định giữ nguyên chiến lược AFL."""
        return ScreenerConditions()

    @classmethod
    def default_profile_id(cls) -> str:
        """Profile id của cấu hình mặc định."""
        return cls._profile_id(ScreenerScanRequest())

    async def scan(self, request: ScreenerScanRequest) -> ScreenerScanResponse:
        """Chạy scan theo mode historical/realtime.

        Args:
            request: Cấu hình scan.

        Returns:
            Kết quả kèm số universe/evaluated/matched/skipped.
        """
        symbols = await self._resolve_symbols(request)
        profile_id = self._profile_id(request)
        if request.mode == "historical":
            response = await self._scan_historical(request, symbols, profile_id)
        else:
            response = await self._scan_realtime(request, symbols, profile_id)
            await self._store_results(response)
            await self._store_active_profile(request, profile_id, symbols, response)
        logger.info(
            "screener_scan_done",
            extra={
                "mode": request.mode,
                "universe": len(symbols),
                "evaluated": response.evaluated,
                "matched": response.matched,
                "skipped": response.skipped,
            },
        )
        return response

    async def active_profile_id(self) -> str | None:
        """Profile realtime đang active, nếu có."""
        if self._redis is None:
            return None
        raw = await self._redis.get(_ACTIVE_PROFILE_KEY)
        if raw is None:
            return None
        try:
            return str(json.loads(raw)["profile_id"])
        except (ValueError, KeyError, TypeError):
            return None

    async def results(self, profile_id: str) -> ScreenerResultsResponse | None:
        """Đọc snapshot realtime mới nhất từ Redis."""
        if self._redis is None:
            return None
        raw = await self._redis.get(f"{_RESULTS_PREFIX}{profile_id}")
        if raw is None:
            return None
        try:
            return ScreenerResultsResponse.model_validate_json(raw)
        except ValueError:
            logger.warning("screener_results_corrupted", extra={"profile_id": profile_id})
            return None

    async def _resolve_symbols(self, request: ScreenerScanRequest) -> list[str]:
        """Resolve universe all hoặc danh sách symbol."""
        if request.universe == "symbols":
            return list(request.symbols or [])
        rows, _total = await self._repository.list_symbols(
            active_only=True, limit=min(request.limit, self._settings.screener_max_symbols)
        )
        return [row.symbol for row in rows]

    def _divergence_events(
        self, bars: Sequence[CandleBar], condition: DivergenceCondition
    ) -> tuple:
        """Tính event phân kỳ theo window latest/range."""
        date_from, date_to = divergence_window(condition)
        return extract_divergence_events(
            bars,
            days=condition.days,
            basis=condition.basis,
            date_from=date_from,
            date_to=date_to,
            market_tz=self._settings.market_tz,
        )

    async def _scan_historical(
        self,
        request: ScreenerScanRequest,
        symbols: Sequence[str],
        profile_id: str,
    ) -> ScreenerScanResponse:
        """Đánh giá nến đã đóng gần nhất, không dùng dữ liệu tương lai."""
        generated_at = datetime.now(tz=UTC)
        as_of = request.as_of or generated_at
        required = self._required_history(request.conditions)
        history_limit = (
            max(required + 2, self._settings.compute_lookback, 520)
            if request.conditions.divergence.enabled
            else required + 2
        )
        bars_by_symbol = await self._repository.recent_candles_by_symbol(
            list(symbols), limit=history_limit, end=as_of
        )
        results: list[ScreenerRow] = []
        evaluated = 0
        skipped = 0
        as_of_market = as_of.astimezone(self._settings.market_tz)
        for symbol in symbols:
            bars = bars_by_symbol.get(symbol, [])
            # Trước giờ đóng, nến hôm nay trong DB vẫn là provisional: không dùng
            # cho historical scan để tránh tín hiệu từ nến chưa xác nhận.
            if (
                bars
                and bars[-1].time.astimezone(self._settings.market_tz).date() == as_of_market.date()
                and as_of_market.time() < self._settings.market_close
            ):
                bars = bars[:-1]
            if len(bars) < required + 1:
                skipped += 1
                continue
            current = bars[-1]
            divergence_events = (
                self._divergence_events(bars, request.conditions.divergence)
                if request.conditions.divergence.enabled
                else ()
            )
            snapshot = SymbolSnapshot(
                symbol=symbol,
                history=tuple(bars[-required - 1 : -1]),
                current=current,
                status="CONFIRMED",
                updated_at=current.time,
                divergence_events=divergence_events,
            )
            match = self._engine.evaluate(snapshot, request.conditions)
            evaluated += 1
            if match is not None:
                results.append(self.to_result_row(match))
        return ScreenerScanResponse(
            profile_id=profile_id,
            mode="historical",
            as_of=as_of,
            generated_at=generated_at,
            feed_status="OFFLINE",
            total_universe=len(symbols),
            evaluated=evaluated,
            matched=len(results),
            skipped=skipped,
            results=results,
        )

    async def _scan_realtime(
        self,
        request: ScreenerScanRequest,
        symbols: Sequence[str],
        profile_id: str,
    ) -> ScreenerScanResponse:
        """Đánh giá realtime: nến hôm nay đã đóng hoặc batch quote làm provisional."""
        generated_at = datetime.now(tz=UTC)
        now_market = datetime.now(tz=self._settings.market_tz)
        today = now_market.date()
        required = self._required_history(request.conditions)
        history_limit = (
            max(required + 2, self._settings.compute_lookback, 520)
            if request.conditions.divergence.enabled
            else required + 2
        )
        bars_by_symbol = await self._repository.recent_candles_by_symbol(
            list(symbols), limit=history_limit, end=generated_at + timedelta(days=1)
        )

        before_close = now_market.time() < self._settings.market_close
        missing: list[str] = []
        for symbol in symbols:
            bars = bars_by_symbol.get(symbol, [])
            if (
                before_close
                and bars
                and bars[-1].time.astimezone(self._settings.market_tz).date() == today
            ):
                bars = bars[:-1]
                bars_by_symbol[symbol] = bars
            if not bars or bars[-1].time.astimezone(self._settings.market_tz).date() != today:
                missing.append(symbol)

        events_by_symbol = {}
        if request.conditions.divergence.enabled:
            for symbol, bars in bars_by_symbol.items():
                events = self._divergence_events(bars, request.conditions.divergence)
                condition = request.conditions.divergence
                events_by_symbol[symbol] = tuple(
                    event
                    for event in events
                    if (condition.direction == "any" or event.direction == condition.direction)
                    and (condition.kind == "any" or event.kind == condition.kind)
                )
            missing = [symbol for symbol in missing if events_by_symbol.get(symbol)]
        quotes = await self._fetch_quotes(missing, today)
        results: list[ScreenerRow] = []
        evaluated = 0
        skipped = 0
        live_count = 0
        for symbol in symbols:
            bars = bars_by_symbol.get(symbol, [])
            if not bars:
                skipped += 1
                continue
            today_bar = bars[-1].time.astimezone(self._settings.market_tz).date() == today
            if today_bar:
                current = bars[-1]
                status: ScreenerStatus = "CONFIRMED"
                updated_at = current.time
                history = bars[-required - 1 : -1]
                live_count += 1
            elif symbol in quotes:
                quote = quotes[symbol]
                current = CandleBar(
                    time=generated_at,
                    open=quote.price,
                    high=quote.price,
                    low=quote.price,
                    close=quote.price,
                    volume=float(quote.volume or 0),
                )
                status = "LIVE"
                updated_at = generated_at
                history = bars[-required:]
                live_count += 1
            else:
                # Không có quote: vẫn đánh giá nến đóng cuối nhưng gắn STALE.
                current = bars[-1]
                status = "STALE"
                updated_at = current.time
                history = bars[-required - 1 : -1]
            if len(history) < required:
                skipped += 1
                continue
            divergence_events = events_by_symbol.get(symbol, ())
            snapshot = SymbolSnapshot(
                symbol=symbol,
                history=tuple(history),
                current=current,
                status=status,
                updated_at=updated_at,
                divergence_events=divergence_events,
            )
            match = self._engine.evaluate(snapshot, request.conditions)
            evaluated += 1
            if match is not None:
                results.append(self.to_result_row(match))
        return ScreenerScanResponse(
            profile_id=profile_id,
            mode="realtime",
            as_of=generated_at,
            generated_at=generated_at,
            feed_status="LIVE" if live_count else "STALE",
            total_universe=len(symbols),
            evaluated=evaluated,
            matched=len(results),
            skipped=skipped,
            results=results,
        )

    async def _fetch_quotes(self, symbols: Sequence[str], today) -> dict:
        """Lấy batch quote theo lô, lỗi upstream trả rỗng để scan chuyển STALE."""
        return await fetch_batch_quotes(
            self._fcdata,
            self._settings,
            symbols,
            today,
        )

    async def _store_results(self, response: ScreenerScanResponse) -> None:
        """Lưu snapshot realtime vào Redis cho `/screener/results` và reconnect."""
        if self._redis is None:
            return
        try:
            await self._redis.set(
                f"{_RESULTS_PREFIX}{response.profile_id}",
                response.model_dump_json(),
                ex=self._settings.screener_results_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - scan vẫn trả kết quả
            logger.warning(
                "screener_results_store_failed",
                extra={"error_type": type(exc).__name__},
            )

    async def _store_active_profile(
        self,
        request: ScreenerScanRequest,
        profile_id: str,
        symbols: Sequence[str],
        response: ScreenerScanResponse,
    ) -> None:
        """Lưu profile realtime đang hoạt động để producer dùng đúng filter."""
        if self._redis is None:
            return
        payload = {
            "profile_id": profile_id,
            "request": request.model_dump(mode="json"),
            "symbols": list(symbols),
            "snapshot": response.model_dump(mode="json"),
        }
        try:
            await self._redis.set(
                _ACTIVE_PROFILE_KEY,
                json.dumps(payload, ensure_ascii=False),
                ex=self._settings.screener_results_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "screener_active_profile_store_failed",
                extra={"error_type": type(exc).__name__},
            )

    @staticmethod
    def _required_history(conditions: ScreenerConditions) -> int:
        """Số nến đã đóng cần trước bar hiện tại.

        Luôn lấy đủ cho cả DK3 và DK6 dù điều kiện bị tắt, để bảng kết quả vẫn
        hiển thị Vol/SMA10 và MA5 chính xác.
        """
        return max(
            1,
            conditions.relative_volume.lookback,
            conditions.price_vs_ma5.period - 1,
        )

    @staticmethod
    def _profile_id(request: ScreenerScanRequest) -> str:
        """Hash cấu hình để FE subscribe đúng channel realtime."""
        payload = request.model_dump(
            mode="json",
            exclude={"limit", "mode", "as_of"},
        )
        payload["symbols"] = sorted(payload.get("symbols") or [])
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    @staticmethod
    def to_result_row(match) -> ScreenerRow:
        """Chuyển match thuần Python sang schema API."""
        return ScreenerRow(
            symbol=match.symbol,
            price=match.price,
            change_percent=match.change_percent,
            volume=match.volume,
            volume_sma=match.volume_sma,
            relative_volume=match.relative_volume,
            ma5=match.ma5,
            price_vs_ma5_percent=match.price_vs_ma5_percent,
            status=match.status,
            updated_at=match.updated_at,
            matched_conditions=list(match.matched_conditions),
            divergence=(
                f"{match.divergence.direction.title()} {match.divergence.kind} · "
                f"{' + '.join(match.divergence.indicators)}"
                if match.divergence is not None
                else None
            ),
        )


async def fetch_batch_quotes(
    client: FcDataClient,
    settings: Settings,
    symbols: Sequence[str],
    today,
) -> dict[str, QuoteRecord]:
    """Lấy quote tích luỹ phiên cho nhiều symbol theo lô.

    Dùng volume `TotalMatchVol` của gateway, không dùng volume lô tick. Lỗi
    upstream trả rỗng để screener chuyển trạng thái STALE thay vì phát tín hiệu
    giả.

    Args:
        client: FCData client.
        settings: Cấu hình batch size.
        symbols: Danh sách symbol.
        today: Ngày giao dịch theo múi giờ thị trường.

    Returns:
        Dict symbol -> `QuoteRecord`.
    """
    if not symbols:
        return {}
    end = format_vn_date(today)
    chunks = [
        list(symbols[index : index + settings.screener_quote_batch_size])
        for index in range(0, len(symbols), settings.screener_quote_batch_size)
    ]

    async def fetch(chunk: list[str]) -> dict[str, QuoteRecord]:
        try:
            rows = await client.fetch_batch_stock_prices(chunk, start=end, end=end)
        except Exception as exc:  # noqa: BLE001 - realtime fallback sang STALE
            logger.warning("screener_quote_failed", extra={"error_type": type(exc).__name__})
            return {}
        mapped: dict[str, QuoteRecord] = {}
        for row in rows:
            symbol = str(
                row.get("Symbol") or row.get("symbol") or row.get("StockSymbol") or ""
            ).upper()
            if not symbol:
                continue
            record = map_quote_row(row, symbol=symbol)
            if (
                record.as_of is not None
                and record.as_of.astimezone(settings.market_tz).date() == today
                and record.price is not None
                and record.price > 0
                and record.volume is not None
                and record.volume >= 0
            ):
                mapped[symbol] = record
        return mapped

    merged: dict[str, QuoteRecord] = {}

    async def collect(chunk: list[str]) -> None:
        merged.update(await fetch(chunk))

    try:
        async with asyncio.timeout(6):
            await asyncio.gather(*(collect(chunk) for chunk in chunks))
    except TimeoutError:
        logger.warning(
            "screener_quote_deadline",
            extra={"requested": len(symbols), "received": len(merged)},
        )
    return merged


def divergence_window(
    condition: DivergenceCondition,
) -> tuple[date | None, date | None]:
    """Trả khoảng ngày hiệu lực cho filter divergence.

    `latest` trả None để engine lọc theo `days` phiên đóng gần nhất (1 = phiên
    gần nhất, tránh lỗi cuối tuần/ngày lễ). `range` dùng ngày người dùng chọn.
    """
    if condition.window == "range":
        return condition.date_from, condition.date_to
    return None, None
