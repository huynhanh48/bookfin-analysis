"""Consumer WebSocket realtime của BookFin FCData Gateway.

Chỉ worker chạy module này, không chạy trong process Uvicorn: mỗi tiến trình chỉ
giữ một kết nối để tránh nhân tick và dữ liệu trùng.

Giao thức thật của gateway (đã kiểm chứng trong `references/gateway-contract.md`):

- Ngay sau khi kết nối, server gửi `{"type": "welcome", ..., "protocol": "subscribe_v1"}`.
- Đăng ký bằng `{"action": "subscribe", "target": "symbol"|"index"|"all", "codes": [...]}`.
- Server không tự ping; client phải gửi `{"action": "ping"}` trước ngưỡng idle 90 giây.
- Tick được forward nguyên văn object SSI `{"DataType": ..., "Content": ...}`, không
  có envelope `{"type": "quote", "data": ...}` như tài liệu mô tả.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.asyncio.client import connect as ws_connect

from app.core.config import Settings
from app.core.logging import get_logger
from app.fcdata.mapper import to_float, to_int

logger = get_logger(__name__)

CONTROL_MESSAGE_TYPES = frozenset({"welcome", "subscribed", "pong", "error", "ssi"})
MAX_CODES_PER_SUBSCRIBE = 200
RECONNECT_MAX_DELAY_SECONDS = 30.0


class WebSocketConnection(Protocol):
    """Phần giao diện của WebSocket mà stream cần."""

    async def send(self, message: str) -> None:
        """Gửi một frame text."""
        ...

    async def recv(self) -> str | bytes:
        """Nhận một frame."""
        ...

    async def close(self) -> None:
        """Đóng kết nối."""
        ...


ConnectFactory = Callable[[str], AbstractAsyncContextManager[WebSocketConnection]]
TickHandler = Callable[["QuoteTick"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class QuoteTick:
    """Một tick đã chuẩn hoá từ dòng dữ liệu realtime.

    Attributes:
        symbol: Mã cổ phiếu hoặc mã chỉ số.
        data_type: Kênh dữ liệu SSI, ví dụ X-TRADE hoặc MI.
        price: Giá khớp gần nhất nếu có.
        volume: Khối lượng khớp lũy kế hoặc khối lượng lô gần nhất.
        raw_time: Chuỗi thời gian thô trong payload.
    """

    symbol: str
    data_type: str
    price: float | None = None
    volume: int | None = None
    raw_time: str | None = None


def build_subscribe_message(target: str, codes: list[str] | None = None) -> str:
    """Tạo message subscribe đúng định dạng gateway chấp nhận.

    Args:
        target: `symbol`, `index` hoặc `all`.
        codes: Danh sách mã; bỏ trống khi target là `all`.

    Returns:
        Chuỗi JSON gửi lên gateway.
    """
    payload: dict[str, Any] = {"action": "subscribe", "target": target}
    if codes:
        payload["codes"] = [code.strip().upper() for code in codes if code.strip()]
    return json.dumps(payload)


def build_ping_message() -> str:
    """Tạo message ping giữ kết nối khỏi bị đóng vì idle.

    Returns:
        Chuỗi JSON `{"action": "ping"}`.
    """
    return json.dumps({"action": "ping"})


def _decode_content(raw: dict[str, Any]) -> dict[str, Any]:
    """Giải mã trường `Content` của object SSI (chuỗi JSON hoặc object)."""
    content = raw.get("Content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def parse_quote_tick(raw: dict[str, Any]) -> QuoteTick | None:
    """Chuẩn hoá một message realtime thành `QuoteTick`.

    Chấp nhận cả object SSI thô (`DataType` + `Content`) và envelope dạng tài liệu
    `{"type": "quote", "data": {...}}`.

    Args:
        raw: Payload JSON đã parse.

    Returns:
        `QuoteTick` hoặc None khi message không chứa dữ liệu thị trường.
    """
    if raw.get("type") == "quote" and isinstance(raw.get("data"), dict):
        raw = raw["data"]

    data_type = str(raw.get("DataType") or raw.get("Channel") or "").strip()
    # Object SSI bọc dữ liệu trong `Content`; envelope dạng tài liệu thì dữ liệu
    # nằm ngay trên object, nên phải chấp nhận cả hai.
    content = _decode_content(raw) or raw

    symbol = (
        content.get("Symbol")
        or content.get("symbol")
        or raw.get("Symbol")
        or raw.get("Code")
        or content.get("IndexId")
        or content.get("IndexID")
        or raw.get("IndexId")
    )
    if not symbol:
        return None

    price = to_float(
        content.get("LastPrice")
        or content.get("EstMatchedPrice")
        or content.get("Close")
        or content.get("IndexValue")
        or content.get("Avg")
        or content.get("AvgPrice")
    )
    volume = to_int(content.get("LastVol") or content.get("TotalVol") or content.get("AllQty"))

    return QuoteTick(
        symbol=str(symbol).strip().upper(),
        data_type=data_type,
        price=price,
        volume=volume,
        raw_time=content.get("Time") or content.get("TradingDate"),
    )


class FcDataStream:
    """Vòng đời kết nối WebSocket: subscribe, heartbeat và tự kết nối lại."""

    def __init__(
        self,
        settings: Settings,
        *,
        on_tick: TickHandler,
        symbols: list[str] | None = None,
        indices: list[str] | None = None,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        """Khởi tạo stream.

        Args:
            settings: Cấu hình ứng dụng.
            on_tick: Callback async nhận từng tick đã chuẩn hoá.
            symbols: Danh sách mã cổ phiếu cần đăng ký.
            indices: Danh sách mã chỉ số cần đăng ký.
            connect_factory: Factory kết nối, dùng để inject WebSocket giả trong test.
        """
        self._settings = settings
        self._on_tick = on_tick
        self._symbols = [code.strip().upper() for code in (symbols or []) if code.strip()]
        self._indices = [code.strip().upper() for code in (indices or []) if code.strip()]
        self._connect = connect_factory or (
            lambda url: ws_connect(
                url, max_size=2**20, open_timeout=settings.fcdata_timeout_seconds
            )
        )
        self._reconnects = 0
        self._last_message_at: float | None = None

    @property
    def reconnect_count(self) -> int:
        """Số lần đã kết nối lại kể từ khi worker khởi động."""
        return self._reconnects

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Chạy vòng lặp kết nối cho tới khi bị dừng.

        Args:
            stop_event: Sự kiện dừng; None nghĩa là chạy mãi.
        """
        stop = stop_event or asyncio.Event()
        delay = 1.0

        while not stop.is_set():
            try:
                async with self._connect(self._settings.fcdata_ws_url) as connection:
                    delay = 1.0
                    await self._session(connection, stop)
            except Exception as exc:  # noqa: BLE001 - mọi lỗi kết nối đều phải retry
                logger.warning(
                    "stream_connection_failed",
                    extra={"error_type": type(exc).__name__, "reconnects": self._reconnects},
                )

            if stop.is_set():
                break

            self._reconnects += 1
            logger.info("stream_reconnecting", extra={"delay_seconds": delay})
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                break
            except TimeoutError:
                delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)

    async def _session(self, connection: WebSocketConnection, stop: asyncio.Event) -> None:
        """Chạy một phiên kết nối: chờ welcome, subscribe, heartbeat, đọc tick."""
        await self._await_welcome(connection)

        if self._symbols:
            await self._subscribe(connection, "symbol", self._symbols)
        if self._indices:
            await self._subscribe(connection, "index", self._indices)

        heartbeat = asyncio.create_task(self._heartbeat_loop(connection, stop))
        try:
            while not stop.is_set():
                frame = await connection.recv()
                await self._handle_frame(frame)
        finally:
            heartbeat.cancel()

    async def _await_welcome(self, connection: WebSocketConnection) -> None:
        """Đọc message chào đầu tiên và ghi log protocol."""
        try:
            frame = await asyncio.wait_for(
                connection.recv(), timeout=self._settings.fcdata_timeout_seconds
            )
        except TimeoutError:
            logger.warning("stream_welcome_timeout")
            return

        payload = self._decode_frame(frame)
        if payload is not None:
            logger.info(
                "stream_welcome",
                extra={
                    "protocol": payload.get("protocol"),
                    "ssi_ready": payload.get("ssiReady"),
                },
            )

    async def _subscribe(
        self, connection: WebSocketConnection, target: str, codes: list[str]
    ) -> None:
        """Đăng ký theo lô, tôn trọng giới hạn mã mỗi lần của gateway."""
        limit = min(self._settings.fcdata_ws_max_codes, MAX_CODES_PER_SUBSCRIBE)
        for start in range(0, len(codes), limit):
            chunk = codes[start : start + limit]
            await connection.send(build_subscribe_message(target, chunk))
            logger.info("stream_subscribed", extra={"target": target, "count": len(chunk)})

    async def _heartbeat_loop(self, connection: WebSocketConnection, stop: asyncio.Event) -> None:
        """Gửi ping định kỳ để không bị gateway đóng vì idle."""
        interval = self._settings.fcdata_ws_ping_seconds
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                try:
                    await connection.send(build_ping_message())
                except Exception as exc:  # noqa: BLE001 - mất kết nối sẽ do vòng ngoài xử lý
                    logger.warning("stream_ping_failed", extra={"error_type": type(exc).__name__})
                    return

    async def _handle_frame(self, frame: str | bytes) -> None:
        """Xử lý một frame nhận từ gateway."""
        payload = self._decode_frame(frame)
        if payload is None:
            return

        message_type = payload.get("type")
        if isinstance(message_type, str) and message_type in CONTROL_MESSAGE_TYPES:
            if message_type == "error":
                logger.warning(
                    "stream_error",
                    extra={"code": payload.get("code"), "detail": payload.get("message")},
                )
            return

        tick = parse_quote_tick(payload)
        if tick is not None:
            await self._on_tick(tick)

    @staticmethod
    def _decode_frame(frame: str | bytes) -> dict[str, Any] | None:
        """Parse frame JSON; frame lạ bị bỏ qua im lặng như hành vi của gateway."""
        if isinstance(frame, bytes):
            try:
                frame = frame.decode("utf-8")
            except UnicodeDecodeError:
                return None
        try:
            payload = json.loads(frame)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None


async def iter_ticks(
    settings: Settings,
    *,
    symbols: list[str] | None = None,
    indices: list[str] | None = None,
) -> AsyncIterator[QuoteTick]:
    """Tiện ích async generator để test hoặc dùng trong script nhỏ.

    Args:
        settings: Cấu hình ứng dụng.
        symbols: Danh sách mã cổ phiếu.
        indices: Danh sách mã chỉ số.

    Yields:
        Từng `QuoteTick` nhận được từ gateway.
    """
    queue: asyncio.Queue[QuoteTick] = asyncio.Queue()

    async def handler(tick: QuoteTick) -> None:
        await queue.put(tick)

    stream = FcDataStream(settings, on_tick=handler, symbols=symbols, indices=indices)
    task = asyncio.create_task(stream.run())
    try:
        while True:
            yield await queue.get()
    finally:
        task.cancel()
