"""Structured logging dạng JSON và request id theo context.

Log được ghi ra stdout dưới dạng một dòng JSON để dễ thu thập bằng container
logging. Mọi bản ghi tự động kèm `request_id` nếu đang trong ngữ cảnh request.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
worker_job_var: ContextVar[str | None] = ContextVar("worker_job", default=None)
worker_run_id_var: ContextVar[str | None] = ContextVar("worker_run_id", default=None)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED_LOG_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Format bản ghi log thành một dòng JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Chuyển một `LogRecord` thành chuỗi JSON.

        Args:
            record: Bản ghi log do thư viện `logging` tạo ra.

        Returns:
            Chuỗi JSON một dòng, gồm các field chuẩn và mọi field bổ sung được
            truyền qua `extra` (ví dụ `method`, `status`, `duration_ms`).
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if job_id_var.get():
            payload["job_id"] = job_id_var.get()
        if case_id_var.get():
            payload["case_id"] = case_id_var.get()
        if worker_job_var.get():
            payload["worker_job"] = worker_job_var.get()
        if worker_run_id_var.get():
            payload["worker_run_id"] = worker_run_id_var.get()
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Cấu hình logging một lần cho toàn bộ process.

    Args:
        level: Mức log tối thiểu, ví dụ INFO hoặc DEBUG.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn tự gắn handler riêng; chuyển tiếp để log không bị ghi hai lần.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Trả về logger trong namespace của ứng dụng.

    Args:
        name: Tên logger, thường là `__name__`.

    Returns:
        Logger đã đi qua cấu hình JSON ở root.
    """
    return logging.getLogger(name)
