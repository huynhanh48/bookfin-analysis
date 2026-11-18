"""Exception của ứng dụng và mapping sang HTTP response.

Mọi lỗi trả về client dùng chung một envelope gồm `code`, `message`, `details` và
`request_id`. Không bao giờ trả stack trace hay thông điệp lỗi thô của upstream.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)


class AppError(Exception):
    """Lỗi có mã, HTTP status và thông điệp an toàn để trả cho client.

    Attributes:
        code: Mã lỗi ổn định cho client xử lý (snake_case).
        status_code: HTTP status tương ứng.
        message: Thông điệp ngắn, an toàn để hiển thị.
        details: Dữ liệu bổ sung, không chứa thông tin nhạy cảm.
    """

    code: str = "internal_error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequestError(AppError):
    """Tham số không hợp lệ về mặt nghiệp vụ, khác lỗi validate schema."""

    code = "invalid_request"
    status_code = status.HTTP_400_BAD_REQUEST


class NotFoundError(AppError):
    """Không tìm thấy dữ liệu được yêu cầu."""

    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class InsufficientHistoryError(AppError):
    """Không đủ số nến để tính chỉ báo hoặc cấu trúc thị trường."""

    code = "insufficient_history"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class DataQualityError(AppError):
    """Dữ liệu nến không đạt kiểm tra chất lượng và bị quarantine."""

    code = "data_quality_error"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class UpstreamError(AppError):
    """BookFin FCData Gateway trả lỗi hoặc dữ liệu không đúng contract."""

    code = "upstream_error"
    status_code = status.HTTP_502_BAD_GATEWAY


class UpstreamTimeoutError(UpstreamError):
    """Gateway không phản hồi trong thời gian cho phép."""

    code = "upstream_timeout"
    status_code = status.HTTP_504_GATEWAY_TIMEOUT


class ConfigurationError(AppError):
    """Cấu hình sai khiến service không thể hoạt động đúng."""

    code = "configuration_error"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class AdminAuthError(AppError):
    """Endpoint quản trị yêu cầu `X-Admin-Key` hợp lệ."""

    code = "admin_auth_required"
    status_code = status.HTTP_403_FORBIDDEN


class InsufficientDataError(AppError):
    """Chưa nạp đủ dữ liệu để thực hiện yêu cầu."""

    code = "insufficient_data"
    status_code = status.HTTP_404_NOT_FOUND


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Tạo response lỗi theo envelope thống nhất.

    Args:
        status_code: HTTP status của response.
        code: Mã lỗi cho client.
        message: Thông điệp ngắn, an toàn để hiển thị.
        details: Chi tiết bổ sung tuỳ chọn.

    Returns:
        `JSONResponse` chứa envelope lỗi kèm request id hiện tại.
    """
    payload: dict[str, Any] = {"code": code, "message": message}
    if details:
        payload["details"] = details
    request_id = request_id_var.get()
    if request_id is not None:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status_code, content={"error": payload})


def register_exception_handlers(app: FastAPI) -> None:
    """Gắn toàn bộ exception handler vào ứng dụng.

    Args:
        app: FastAPI application cần gắn handler.
    """

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error("app_error", extra={"code": exc.code, "detail": exc.message})
        else:
            logger.warning("app_error", extra={"code": exc.code, "detail": exc.message})
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="Tham số request không hợp lệ.",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            code=f"http_{exc.status_code}",
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="Lỗi hệ thống, vui lòng thử lại sau.",
        )
