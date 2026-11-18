"""Middleware log truy cập dưới dạng structured log.

Mỗi request được ghi một dòng JSON gồm method, path, status và thời gian xử lý.
Endpoint health/ready bị loại khỏi log để tránh nhiễu do probe định kỳ.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger("app.access")

_SILENT_PATHS = frozenset({"/health", "/ready"})


class AccessLoggingMiddleware(BaseHTTPMiddleware):
    """Ghi log mỗi request kèm thời gian xử lý."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Đo thời gian và ghi log cho một request.

        Args:
            request: Request đến từ client.
            call_next: Hàm gọi tầng xử lý phía trong.

        Returns:
            Response của ứng dụng.

        Raises:
            Exception: Mọi lỗi từ tầng trong được ghi log rồi raise lại để
                exception handler chung xử lý.
        """
        if request.url.path in _SILENT_PATHS:
            return await call_next(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "http_request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                },
            )
            raise

        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "client": request.client.host if request.client else None,
            },
        )
        return response
