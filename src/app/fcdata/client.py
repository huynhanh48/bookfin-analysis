"""HTTP client gọi BookFin FCData Gateway.

Client này là lớp adapter duy nhất biết tới HTTP và các đặc thù của gateway:
proxy SSI trả HTTP 200 kèm `status: "Error"`/`"NoDataFound"`, số được trả dạng
chuỗi PascalCase, và phân trang dùng `pageIndex`/`pageSize` thay vì offset.

Mọi request đi qua một `httpx.AsyncClient` dùng chung (connection pooling),
timeout cấu hình được, semaphore giới hạn số request đồng thời và retry có giới
hạn cho lỗi tạm thời. Không bao giờ retry lỗi 4xx.
"""

import asyncio
import random
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.config import Settings
from app.core.exceptions import UpstreamError, UpstreamTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_DEFAULT_PAGE_SIZE = 100


class FcDataClient:
    """Adapter HTTP tới BookFin FCData Gateway."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Khởi tạo client.

        Args:
            settings: Cấu hình chứa base URL, timeout, retry và concurrency.
            transport: Transport tuỳ chọn, dùng để inject mock trong test.
        """
        self._settings = settings
        headers = {"Accept": "application/json", "User-Agent": f"{settings.app_name}/0.1"}
        api_key = settings.fcdata_api_key
        if api_key:
            headers["X-API-Key"] = api_key

        self._client = httpx.AsyncClient(
            base_url=settings.fcdata_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.fcdata_timeout_seconds),
            headers=headers,
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(settings.fcdata_max_concurrency)
        self._max_retries = settings.fcdata_max_retries

    async def __aenter__(self) -> Self:
        """Cho phép dùng client như async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Đóng client khi thoát khỏi context."""
        await self.close()

    async def close(self) -> None:
        """Đóng connection pool của client."""
        await self._client.aclose()

    async def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        """Chờ trước khi retry với exponential backoff kèm jitter.

        Args:
            attempt: Số lần đã thử, bắt đầu từ 0.
            response: Response gần nhất nếu có, để tôn trọng header `Retry-After`.
        """
        delay = min(0.5 * (2**attempt), 8.0)
        delay *= 0.5 + random.random()
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
        await asyncio.sleep(delay)

    async def request_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        method: str = "GET",
    ) -> dict[str, Any]:
        """Gọi một endpoint và trả JSON đã parse.

        Args:
            path: Đường dẫn tương đối, ví dụ `/health`.
            params: Query parameters; giá trị None hoặc chuỗi rỗng bị bỏ.
            json_body: Body JSON cho request POST.
            method: HTTP method, mặc định GET.

        Returns:
            Body JSON dạng dict.

        Raises:
            UpstreamTimeoutError: Khi gateway không phản hồi kịp.
            UpstreamError: Khi gateway trả lỗi HTTP hoặc JSON không hợp lệ.
        """
        clean_params = {
            key: value for key, value in (params or {}).items() if value not in (None, "")
        }

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = asyncio.get_running_loop().time()
            try:
                async with self._semaphore:
                    response = await self._client.request(
                        method, path, params=clean_params, json=json_body
                    )
            except httpx.TimeoutException as exc:
                last_error = UpstreamTimeoutError(f"Timeout khi gọi {path}.")
                if attempt < self._max_retries:
                    logger.warning(
                        "fcdata_timeout",
                        extra={"path": path, "attempt": attempt + 1},
                    )
                    await self._backoff(attempt)
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = UpstreamError(f"Lỗi mạng khi gọi {path}: {type(exc).__name__}")
                if attempt < self._max_retries:
                    logger.warning(
                        "fcdata_network_error",
                        extra={"path": path, "attempt": attempt + 1, "error": type(exc).__name__},
                    )
                    await self._backoff(attempt)
                    continue
                raise last_error from exc

            duration_ms = round((asyncio.get_running_loop().time() - started) * 1000, 2)
            logger.info(
                "fcdata_request",
                extra={
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "attempt": attempt + 1,
                },
            )

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                await self._backoff(attempt, response)
                continue

            if response.status_code >= 400:
                raise UpstreamError(
                    f"Gateway trả HTTP {response.status_code} cho {path}.",
                    details={"status_code": response.status_code, "path": path},
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamError(f"Gateway trả JSON không hợp lệ cho {path}.") from exc

            if not isinstance(payload, dict):
                raise UpstreamError(f"Gateway trả payload không phải object cho {path}.")
            return payload

        raise last_error or UpstreamError(f"Không gọi được {path}.")

    @staticmethod
    def _check_status(payload: dict[str, Any], *, path: str, allow_empty: bool = False) -> None:
        """Kiểm tra `status` nội bộ của gateway.

        Gateway trả HTTP 200 kèm `status: "Error"` hoặc `"NoDataFound"`, nên phải
        đọc field này thay vì chỉ dựa vào HTTP status.

        Args:
            payload: Body JSON đã parse.
            path: Đường dẫn dùng cho thông điệp lỗi.
            allow_empty: Cho phép `NoDataFound` coi như kết quả rỗng.

        Raises:
            UpstreamError: Khi gateway báo lỗi hoặc không có dữ liệu.
        """
        status = payload.get("status")
        message = str(payload.get("message") or "")
        if isinstance(status, int) or status in (None, "Success"):
            return
        if status in ("Error", "NoDataFound"):
            if allow_empty and status == "NoDataFound":
                return
            raise UpstreamError(message or f"Gateway báo lỗi cho {path}.")

    async def health(self) -> dict[str, Any]:
        """Đọc trạng thái dịch vụ gateway.

        Returns:
            Payload của `/health`.
        """
        return await self.request_json("/health")

    async def fetch_catalog(self) -> dict[str, Any]:
        """Tải toàn bộ danh mục mã chứng khoán.

        Returns:
            Payload thô của `/api/v1/catalogs/master`, gồm `data` và `totalRecord`.
        """
        payload = await self.request_json("/api/v1/catalogs/master")
        self._check_status(payload, path="/api/v1/catalogs/master", allow_empty=True)
        return payload

    async def fetch_history(
        self, symbol: str, *, limit: int = 250, timeframe: str = "1D"
    ) -> dict[str, Any]:
        """Tải lịch sử nến đã chuẩn hoá của một mã.

        Args:
            symbol: Mã chứng khoán.
            limit: Số nến cần lấy.
            timeframe: Khung thời gian.

        Returns:
            Payload `data` của `/api/market/history/{symbol}`.
        """
        payload = await self.request_json(
            f"/api/market/history/{symbol.upper()}",
            params={"limit": limit, "timeframe": timeframe},
        )
        self._check_status(payload, path=f"/api/market/history/{symbol}", allow_empty=True)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def fetch_daily_ohlc(
        self,
        symbol: str,
        *,
        start: str,
        end: str,
        page_index: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        ascending: bool = True,
    ) -> dict[str, Any]:
        """Tải nến ngày thô từ proxy SSI `/DailyOhlc`.

        Args:
            symbol: Mã chứng khoán.
            start: Ngày bắt đầu dạng DD/MM/YYYY.
            end: Ngày kết thúc dạng DD/MM/YYYY.
            page_index: Trang, bắt đầu từ 1.
            page_size: Số bản ghi mỗi trang.
            ascending: True để nhận dữ liệu cũ tới mới.

        Returns:
            Payload thô gồm `data` và `totalRecord`.
        """
        payload = await self.request_json(
            "/DailyOhlc",
            params={
                "symbol": symbol.upper(),
                "fromDate": start,
                "toDate": end,
                "pageIndex": page_index,
                "pageSize": page_size,
                "ascending": str(ascending).lower(),
            },
        )
        self._check_status(payload, path="/DailyOhlc", allow_empty=True)
        return payload

    async def fetch_daily_stock_price(
        self,
        symbol: str,
        *,
        market: str = "HOSE",
        start: str,
        end: str,
        page_index: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Tải giá khớp lệnh ngày từ proxy SSI `/DailyStockPrice`.

        Args:
            symbol: Mã chứng khoán.
            market: Sàn giao dịch, mặc định HOSE.
            start: Ngày bắt đầu dạng DD/MM/YYYY.
            end: Ngày kết thúc dạng DD/MM/YYYY.
            page_index: Trang, bắt đầu từ 1.
            page_size: Số bản ghi mỗi trang.

        Returns:
            Payload thô gồm `data` và `totalRecord`.
        """
        payload = await self.request_json(
            "/DailyStockPrice",
            params={
                "symbol": symbol.upper(),
                "market": market,
                "fromDate": start,
                "toDate": end,
                "pageIndex": page_index,
                "pageSize": page_size,
            },
        )
        self._check_status(payload, path="/DailyStockPrice", allow_empty=True)
        return payload

    async def fetch_batch_stock_prices(
        self,
        symbols: list[str],
        *,
        start: str,
        end: str,
        market: str = "HOSE",
    ) -> list[dict[str, Any]]:
        """Tải snapshot giá mới nhất của nhiều mã trong một request.

        Args:
            symbols: Danh sách mã chứng khoán.
            start: Ngày bắt đầu dạng DD/MM/YYYY, bắt buộc theo contract gateway.
            end: Ngày kết thúc dạng DD/MM/YYYY, bắt buộc theo contract gateway.
            market: Sàn giao dịch, mặc định HOSE.

        Returns:
            Danh sách row `DailyStockPrice`, mỗi mã tối đa một row.
        """
        if not symbols:
            return []
        payload = await self.request_json(
            "/BatchStockPrices",
            params={
                "symbols": ",".join(symbol.upper() for symbol in symbols),
                "market": market,
                "fromDate": start,
                "toDate": end,
            },
        )
        self._check_status(payload, path="/BatchStockPrices", allow_empty=True)
        rows = payload.get("data")
        return rows if isinstance(rows, list) else []

    async def fetch_batch_candles(
        self,
        symbols: list[str],
        *,
        limit: int = 250,
        timeframe: str = "1D",
    ) -> dict[str, Any]:
        """Tải nến của nhiều mã qua endpoint batch nội bộ.

        Args:
            symbols: Danh sách mã chứng khoán.
            limit: Số nến mỗi mã.
            timeframe: Khung thời gian.

        Returns:
            Payload thô gồm `data` dạng dict theo mã.
        """
        payload = await self.request_json(
            "/api/market/batch",
            json_body={"symbols": symbols, "timeframe": timeframe, "limit": limit},
            method="POST",
        )
        self._check_status(payload, path="/api/market/batch", allow_empty=True)
        return payload
