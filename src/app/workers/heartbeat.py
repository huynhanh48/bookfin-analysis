"""Heartbeat và healthcheck cho từng role worker.

Mỗi role dài hạn (scheduler, realtime) ghi một file JSON nhỏ theo chu kỳ. Docker
healthcheck gọi `python -m app.workers health --role <role>` để kiểm tra file còn
mới, thay vì phụ thuộc lệnh shell `find -mmin` khó kiểm soát trên image tối giản.

File được ghi qua file tạm rồi `os.replace` để healthcheck không bao giờ đọc
phải nội dung dở dang.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

StatusProvider = Callable[[], dict[str, Any]]


def heartbeat_path(settings: Settings, role: str) -> Path:
    """Đường dẫn heartbeat của một role.

    Scheduler giữ nguyên `SCHEDULER_HEARTBEAT_FILE` để tương thích healthcheck
    cũ; các role khác nằm trong `WORKER_HEARTBEAT_DIR`.

    Args:
        settings: Cấu hình ứng dụng.
        role: Tên role (`scheduler`, `realtime`, ...).

    Returns:
        Đường dẫn file heartbeat.
    """
    if role == "scheduler":
        return Path(settings.scheduler_heartbeat_file)
    return Path(settings.worker_heartbeat_dir) / f"{role}.heartbeat"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Bộ ghi heartbeat cho một role.

    Attributes:
        path: File đích.
        role: Tên role.
        interval_seconds: Chu kỳ ghi khi chạy nền.
    """

    path: Path
    role: str
    interval_seconds: float = 15.0

    def write(
        self,
        *,
        status: str = "ok",
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Ghi đè heartbeat atomically.

        Args:
            status: Trạng thái ngắn (`ok`, `stopping`, `error`).
            detail: Thông tin bổ sung, phải JSON-serializable.
            now: Thời điểm ghi, dùng cho test.
        """
        payload = {
            "role": self.role,
            "pid": os.getpid(),
            "status": status,
            "updated_at": (now or datetime.now(UTC)).isoformat(),
            "detail": detail or {},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            # Heartbeat chỉ phục vụ vận hành; lỗi ghi không được làm chết worker.
            logger.warning(
                "worker_heartbeat_failed",
                extra={"role": self.role, "error_type": type(exc).__name__},
            )

    async def run(
        self,
        stop_event: asyncio.Event,
        *,
        status_provider: StatusProvider | None = None,
        status: str = "ok",
    ) -> None:
        """Ghi heartbeat định kỳ cho tới khi dừng.

        Args:
            stop_event: Sự kiện dừng.
            status_provider: Hàm trả detail mới mỗi vòng.
            status: Trạng thái mặc định.
        """
        while not stop_event.is_set():
            self.write(status=status, detail=status_provider() if status_provider else None)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
        self.write(status="stopping", detail=status_provider() if status_provider else None)


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    """Đọc heartbeat nếu file tồn tại và là JSON hợp lệ.

    Args:
        path: File heartbeat.

    Returns:
        Dict nội dung hoặc None khi thiếu/hỏng.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_is_fresh(
    path: Path,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Kiểm tra heartbeat còn trong ngưỡng cho phép.

    Args:
        path: File heartbeat.
        max_age_seconds: Tuổi tối đa.
        now: Thời điểm so sánh, dùng cho test.

    Returns:
        True khi file tồn tại, JSON hợp lệ và `updated_at` không quá cũ.
    """
    payload = read_heartbeat(path)
    if payload is None:
        return False
    try:
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
    except (KeyError, ValueError):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return (reference - updated_at).total_seconds() <= max_age_seconds


def check_role_health(
    settings: Settings,
    role: str,
    *,
    max_age_seconds: float = 120,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Kiểm tra sức khoẻ role từ heartbeat.

    Args:
        settings: Cấu hình ứng dụng.
        role: Tên role.
        max_age_seconds: Tuổi tối đa của heartbeat.
        now: Thời điểm so sánh.

    Returns:
        Tuple `(ok, payload)`; payload là nội dung heartbeat đọc được (nếu có).
    """
    path = heartbeat_path(settings, role)
    payload = read_heartbeat(path)
    if payload is None:
        return False, None
    return heartbeat_is_fresh(path, max_age_seconds=max_age_seconds, now=now), payload
