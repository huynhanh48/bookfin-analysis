"""Screener realtime producer trong worker và Redis→WebSocket bridge trong API.

Producer chạy trong `RealtimeRole` hiện tại, không tạo container mới. Tick chỉ
đánh dấu symbol dirty; mỗi vòng đánh giá chỉ lấy batch quote cho các symbol thay
đổi, cộng thêm reconcile định kỳ để bù tick thiếu khi FCData `ssiReady=false`.

Bridge trong API process subscribe Redis pub/sub và fan-out vào `RealtimeService`
theo channel `screener:{profile_id}` — một channel cho toàn bộ kết quả, không mở
kết nối riêng cho từng symbol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.fcdata.stream import QuoteTick
from app.indicators.market_structure import CandleBar
from app.schemas.realtime import RealtimeEvent
from app.schemas.screener import (
    ScreenerFeedStatus,
    ScreenerRealtimeEvent,
    ScreenerResultsResponse,
    ScreenerRow,
    ScreenerScanRequest,
    ScreenerStatus,
)
from app.services.realtime_service import RealtimeService
from app.services.screener_engine import (
    DivergenceEventSummary,
    ScreenerEngine,
    SymbolSnapshot,
    extract_divergence_events,
)
from app.services.screener_service import (
    ScreenerService,
    divergence_window,
    fetch_batch_quotes,
)

logger = get_logger(__name__)

_ACTIVE_PROFILE_KEY = "screener:active_profile"
_EVENTS_PREFIX = "screener:events:"
_RESULTS_PREFIX = "screener:results:"

HistoryLoader = Callable[[Sequence[str], int, datetime], Awaitable[dict[str, list[CandleBar]]]]


class ScreenerRealtimeProducer:
    """Đánh giá screener realtime cho universe của realtime worker."""

    def __init__(
        self,
        settings: Settings,
        *,
        symbols: Sequence[str],
        redis: Redis | None,
        fcdata: FcDataClient,
        history_loader: HistoryLoader,
        engine: ScreenerEngine | None = None,
    ) -> None:
        """Khởi tạo producer.

        Args:
            settings: Cấu hình ứng dụng.
            symbols: Universe symbol mà worker đang subscribe.
            redis: Redis client; None thì producer không publish được.
            fcdata: Client lấy batch quote.
            history_loader: Hàm đọc nến đã đóng cho các symbol.
            engine: Screener engine tuỳ chọn cho test.
        """
        self._settings = settings
        self._symbols = {symbol.upper() for symbol in symbols}
        self._redis = redis
        self._fcdata = fcdata
        self._history_loader = history_loader
        self._engine = engine or ScreenerEngine()
        self._dirty: set[str] = set()
        self._history: dict[str, tuple[CandleBar, ...]] = {}
        self._divergence_events: dict[str, tuple[DivergenceEventSummary, ...]] = {}
        self._history_loaded_at = 0.0
        self._history_session: tuple[date, bool] | None = None
        self._last_reconcile = 0.0
        self._profile_id = ScreenerService.default_profile_id()
        self._conditions = ScreenerScanRequest().conditions
        self._profile_symbols: set[str] = set(self._symbols)
        self._matched: set[str] = set()
        self._rows: dict[str, ScreenerRow] = {}
        self._last_transition: dict[str, float] = {}
        self._last_publish = 0.0
        self._last_feed_status = "OFFLINE"
        self._pending_entered: list[ScreenerRow] = []
        self._pending_exited: list[str] = []
        self._pending_feed_status = "OFFLINE"
        self._last_evaluated = 0
        self._last_skipped = 0
        self._scan_seed_at: datetime | None = None

    async def on_tick(self, tick: QuoteTick) -> None:
        """Chỉ đánh dấu symbol thuộc universe là dirty."""
        symbol = tick.symbol.upper()
        if symbol in self._symbols:
            self._dirty.add(symbol)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Vòng lặp đánh giá cho tới khi dừng."""
        if self._redis is None:
            logger.warning("screener_realtime_disabled_no_redis")
            return
        await self._refresh_history(force=True)
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                await self._reload_profile()
                targets = await self._targets()
                if targets:
                    await self._evaluate(list(targets))
                await self._publish_if_needed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - producer không được chết vì một vòng
                logger.warning(
                    "screener_realtime_cycle_failed",
                    extra={"error_type": type(exc).__name__},
                )
            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=max(0.1, self._settings.screener_eval_interval_seconds - elapsed),
                )
            except TimeoutError:
                continue

    async def _reload_profile(self) -> None:
        """Đọc profile realtime đang active; đổi filter thì reset state."""
        raw = await self._redis.get(_ACTIVE_PROFILE_KEY)  # type: ignore[union-attr]
        if raw is None:
            return
        try:
            payload = json.loads(raw)
            request = ScreenerScanRequest.model_validate(payload["request"])
            profile_id = str(payload["profile_id"])
            snapshot = (
                ScreenerResultsResponse.model_validate(payload["snapshot"])
                if "snapshot" in payload
                else None
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("screener_active_profile_invalid")
            return
        if profile_id == self._profile_id:
            self._conditions = request.conditions
            if snapshot is None or (
                self._scan_seed_at is not None and snapshot.generated_at <= self._scan_seed_at
            ):
                return
        self._profile_id = profile_id
        self._conditions = request.conditions
        self._profile_symbols = (
            set(payload["symbols"])
            if "symbols" in payload
            else (
                {symbol.upper() for symbol in request.symbols or []}
                if request.universe == "symbols"
                else set(self._symbols)
            )
        )
        self._matched.clear()
        self._rows.clear()
        self._scan_seed_at = snapshot.generated_at if snapshot else None
        self._pending_entered.clear()
        self._pending_exited.clear()
        self._last_reconcile = float("-inf")
        if snapshot is not None:
            self._rows.update((row.symbol, row) for row in snapshot.results)
            self._matched.update(self._rows)
            self._pending_feed_status = snapshot.feed_status
            self._last_evaluated = snapshot.evaluated
            self._last_skipped = snapshot.skipped
        self._last_transition.clear()
        self._history.clear()
        self._divergence_events.clear()
        self._history_loaded_at = 0.0
        self._history_session = None
        self._last_feed_status = "OFFLINE"
        logger.info("screener_profile_changed", extra={"profile_id": profile_id})

    async def _targets(self) -> set[str]:
        """Dirty symbols, hoặc toàn universe khi tới nhịp reconcile."""
        now = time.monotonic()
        targets = {symbol for symbol in self._dirty if symbol in self._profile_symbols}
        if now - self._last_reconcile >= self._settings.screener_reconcile_seconds:
            targets = set(self._profile_symbols)
            self._last_reconcile = now
        self._dirty.difference_update(targets)
        if targets:
            await self._refresh_history(list(targets))
        return targets

    async def _refresh_history(
        self, symbols: Sequence[str] | None = None, *, force: bool = False
    ) -> None:
        """Nạp/cập nhật cache nến đã đóng cho symbol cần đánh giá."""
        now = time.monotonic()
        market_now = datetime.now(tz=self._settings.market_tz)
        market_session = (market_now.date(), market_now.time() >= self._settings.market_close)
        if (
            not force
            and market_session == self._history_session
            and now - self._history_loaded_at < 300
            and symbols is not None
        ):
            missing = [symbol for symbol in symbols if symbol not in self._history]
            if not missing:
                return
            target = missing
        else:
            # Hết TTL: nạp lại toàn universe để bắt kịp nến hôm nay sau giờ đóng.
            target = list(self._profile_symbols)
            self._history_loaded_at = now
        if not target:
            return
        required = ScreenerService._required_history(self._conditions)
        limit = (
            max(required + 1, self._settings.compute_lookback, 520)
            if self._conditions.divergence.enabled
            else required + 1
        )
        loaded = await self._history_loader(target, limit, datetime.now(tz=UTC) + timedelta(days=1))
        self._history_session = market_session
        today, after_close = market_session
        before_close = not after_close
        for symbol, bars in loaded.items():
            self._history[symbol] = tuple(bars)
            if self._conditions.divergence.enabled:
                confirmed = tuple(bars)
                if (
                    before_close
                    and confirmed
                    and confirmed[-1].time.astimezone(self._settings.market_tz).date() == today
                ):
                    confirmed = confirmed[:-1]
                date_from, date_to = divergence_window(self._conditions.divergence)
                self._divergence_events[symbol] = extract_divergence_events(
                    confirmed,
                    days=self._conditions.divergence.days,
                    basis=self._conditions.divergence.basis,
                    date_from=date_from,
                    date_to=date_to,
                    market_tz=self._settings.market_tz,
                )

    async def _evaluate(self, symbols: Sequence[str]) -> None:
        """Lấy quote batch và cập nhật trạng thái entered/exited."""
        now_market = datetime.now(tz=self._settings.market_tz)
        quotes = await fetch_batch_quotes(
            self._fcdata,
            self._settings,
            list(symbols),
            now_market.date(),
        )
        entered: list[ScreenerRow] = []
        exited: list[str] = []
        now_monotonic = time.monotonic()
        evaluated_count = 0
        for symbol in symbols:
            bars = self._history.get(symbol, ())
            required = ScreenerService._required_history(self._conditions)
            today = now_market.date()
            quote = quotes.get(symbol)
            before_close = now_market.time() < self._settings.market_close
            if (
                before_close
                and bars
                and bars[-1].time.astimezone(self._settings.market_tz).date() == today
            ):
                bars = bars[:-1]
            if bars and bars[-1].time.astimezone(self._settings.market_tz).date() == today:
                current = bars[-1]
                history = bars[-required - 1 : -1]
                status: ScreenerStatus = "CONFIRMED"
            elif quote is not None and quote.price is not None and quote.price > 0:
                if len(bars) < required:
                    continue
                current = CandleBar(
                    time=datetime.now(tz=UTC),
                    open=float(quote.price),
                    high=float(quote.price),
                    low=float(quote.price),
                    close=float(quote.price),
                    volume=float(quote.volume or 0),
                )
                history = bars[-required:]
                status = "LIVE"
            else:
                continue
            if len(history) < required:
                continue
            previous_match = self._rows.get(symbol)
            if (
                status == "LIVE"
                and previous_match is not None
                and previous_match.status == "LIVE"
                and previous_match.updated_at.astimezone(self._settings.market_tz).date() == today
                and current.volume < previous_match.volume
            ):
                # FCData batches can return older snapshots of the same session.
                # A cumulative matched volume regression is not evidence of an exit.
                logger.info(
                    "screener_live_volume_regression",
                    extra={
                        "symbol": symbol,
                        "profile_id": self._profile_id,
                        "previous_volume": previous_match.volume,
                        "received_volume": current.volume,
                    },
                )
                continue
            evaluated_count += 1
            snapshot = SymbolSnapshot(
                symbol=symbol,
                history=history,
                current=current,
                status=status,
                updated_at=current.time,
                divergence_events=self._divergence_events.get(symbol, ()),
            )
            match = self._engine.evaluate(snapshot, self._conditions)
            if match is not None:
                row = ScreenerService.to_result_row(match)
                self._rows[symbol] = row
                if symbol not in self._matched:
                    self._matched.add(symbol)
                    if (
                        now_monotonic - self._last_transition.get(symbol, 0)
                        >= self._settings.screener_event_cooldown_seconds
                    ):
                        entered.append(row)
                        self._last_transition[symbol] = now_monotonic
            else:
                self._rows.pop(symbol, None)
                if symbol in self._matched:
                    logger.info(
                        "screener_symbol_no_longer_matches",
                        extra={
                            "symbol": symbol,
                            "profile_id": self._profile_id,
                            "price": current.close,
                            "volume": current.volume,
                            "previous_close": history[-1].close,
                            "previous_match_price": previous_match.price
                            if previous_match
                            else None,
                            "previous_match_volume": previous_match.volume
                            if previous_match
                            else None,
                        },
                    )
                    self._matched.discard(symbol)
                    if (
                        now_monotonic - self._last_transition.get(symbol, 0)
                        >= self._settings.screener_event_cooldown_seconds
                    ):
                        exited.append(symbol)
                        self._last_transition[symbol] = now_monotonic

        feed_status: ScreenerFeedStatus = "LIVE" if evaluated_count else "STALE"
        self._pending_entered.extend(entered)
        self._pending_exited.extend(exited)
        self._pending_feed_status = feed_status
        self._last_evaluated = evaluated_count
        self._last_skipped = max(0, len(symbols) - evaluated_count)

    async def _publish_if_needed(self) -> None:
        """Publish batch có throttle; không gửi event trùng mỗi tick."""
        now = time.monotonic()
        pending_entered = self._pending_entered
        pending_exited = self._pending_exited
        feed_status: ScreenerFeedStatus = self._pending_feed_status  # type: ignore[assignment]
        changed = bool(pending_entered or pending_exited)
        if (
            not changed
            and now - self._last_publish < self._settings.screener_publish_interval_seconds
        ):
            return
        if not changed and feed_status == self._last_feed_status and now - self._last_publish < 5:
            return
        self._last_publish = now
        rows = sorted(self._rows.values(), key=lambda row: row.symbol)
        event = ScreenerRealtimeEvent(
            profile_id=self._profile_id,
            rows=rows,
            entered=pending_entered[: self._settings.screener_max_events_per_second],
            exited=pending_exited[: self._settings.screener_max_events_per_second],
            feed_status=feed_status,
            generated_at=datetime.now(tz=UTC),
            scan_seed_at=self._scan_seed_at,
        )
        await self._redis.publish(  # type: ignore[union-attr]
            f"{_EVENTS_PREFIX}{self._profile_id}",
            event.model_dump_json(),
        )
        response = ScreenerResultsResponse(
            profile_id=self._profile_id,
            mode="realtime",
            as_of=event.generated_at,
            generated_at=event.generated_at,
            feed_status=feed_status,
            total_universe=len(self._profile_symbols),
            evaluated=self._last_evaluated,
            matched=len(rows),
            skipped=self._last_skipped,
            results=rows,
        )
        await self._redis.set(  # type: ignore[union-attr]
            f"{_RESULTS_PREFIX}{self._profile_id}",
            response.model_dump_json(),
            ex=self._settings.screener_results_ttl_seconds,
        )
        self._last_feed_status = feed_status
        self._pending_entered = []
        self._pending_exited = []


class ScreenerRealtimeBridge:
    """Chuyển Redis pub/sub của worker thành WebSocket event trong API."""

    def __init__(self, redis: Redis | None, hub: RealtimeService) -> None:
        self._redis = redis
        self._hub = hub

    async def run(self, stop_event: asyncio.Event) -> None:
        """Subscribe pattern và fan-out cho tới khi dừng."""
        if self._redis is None:
            return
        while not stop_event.is_set():
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(f"{_EVENTS_PREFIX}*")
                while not stop_event.is_set():
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None:
                        continue
                    await self._forward(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - bridge tự reconnect
                logger.warning("screener_bridge_failed", extra={"error_type": type(exc).__name__})
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.punsubscribe(f"{_EVENTS_PREFIX}*")
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def _forward(self, raw: object) -> None:
        """Forward một message JSON; bỏ qua message hỏng."""
        if not isinstance(raw, str):
            return
        try:
            event = ScreenerRealtimeEvent.model_validate_json(raw)
        except ValueError:
            logger.warning("screener_bridge_invalid_event")
            return
        channel = f"screener:{event.profile_id}"
        for row in event.entered:
            await self._hub.publish(
                RealtimeEvent(
                    type="SCREENER_ENTERED",
                    channel=channel,
                    data={"profile_id": event.profile_id, "row": row.model_dump(mode="json")},
                )
            )
        for symbol in event.exited:
            await self._hub.publish(
                RealtimeEvent(
                    type="SCREENER_EXITED",
                    channel=channel,
                    data={"profile_id": event.profile_id, "symbol": symbol},
                )
            )
        await self._hub.publish(
            RealtimeEvent(
                type="screener.update",
                channel=channel,
                data={
                    "profile_id": event.profile_id,
                    "rows": [row.model_dump(mode="json") for row in event.rows],
                    "feed_status": event.feed_status,
                    "generated_at": event.generated_at.isoformat(),
                    "scan_seed_at": event.scan_seed_at.isoformat() if event.scan_seed_at else None,
                },
            )
        )
