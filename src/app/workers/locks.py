"""Redis distributed lock an toàn cho worker engine.

Điểm quan trọng:

- Mỗi lần acquire dùng một token duy nhất. Release/extend chỉ thao tác khi giá
  trị trong Redis vẫn đúng token đó, tránh trường hợp job A hết TTL, job B đã
  acquire, rồi job A xoá mất khoá của B.
- Khi Redis tắt, engine vẫn chạy được (fail-open mặc định) để không biến sự cố
  cache thành sự cố toàn hệ thống. Có thể đổi bằng `WORKER_LOCK_FAIL_OPEN=false`.
- TTL được gia hạn định kỳ bởi engine để job dài không bị instance khác chiếm.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_EXTEND_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


class DistributedLock(Protocol):
    """Giao diện tối thiểu mà engine cần từ một lock."""

    @property
    def renewal_interval(self) -> float:
        """Chu kỳ gia hạn TTL (giây)."""
        ...

    async def acquire(self) -> bool:
        """Thử lấy khoá, trả False nếu instance khác đang giữ."""
        ...

    async def release(self) -> None:
        """Nhả khoá nếu vẫn sở hữu token."""
        ...

    async def extend(self) -> bool:
        """Gia hạn TTL nếu vẫn sở hữu token."""
        ...


class NullLock:
    """Lock no-op dùng khi Redis bị tắt."""

    @property
    def renewal_interval(self) -> float:
        return 0.0

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None

    async def extend(self) -> bool:
        return True


class RedisLock:
    """Lock Redis dùng token riêng cho từng lần chạy."""

    def __init__(
        self,
        client: Redis | None,
        key: str,
        *,
        ttl_seconds: int,
        token: str | None = None,
        fail_open: bool = True,
    ) -> None:
        """Khởi tạo lock.

        Args:
            client: Redis client; None nghĩa là Redis bị tắt.
            key: Tên khoá Redis.
            ttl_seconds: TTL tính bằng giây.
            token: Token sở hữu; mặc định sinh UUID.
            fail_open: Cho job chạy tiếp khi Redis lỗi thay vì bỏ qua.
        """
        self._client = client
        self._key = key
        self._ttl_seconds = max(1, ttl_seconds)
        self._token = token or uuid.uuid4().hex
        self._fail_open = fail_open
        self._acquired = False

    @property
    def key(self) -> str:
        """Tên khoá Redis."""
        return self._key

    @property
    def token(self) -> str:
        """Token sở hữu khoá."""
        return self._token

    @property
    def renewal_interval(self) -> float:
        """Gia hạn ở 1/3 TTL để có hai lần thử trước khi khoá hết hạn."""
        return max(1.0, self._ttl_seconds / 3)

    async def acquire(self) -> bool:
        """Thử acquire bằng `SET NX EX`.

        Returns:
            True khi lấy được khoá (hoặc fail-open khi Redis lỗi).
        """
        if self._client is None:
            self._acquired = True
            return True
        try:
            acquired = await self._client.set(self._key, self._token, nx=True, ex=self._ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - Redis lỗi không được làm chết worker
            logger.warning(
                "worker_lock_acquire_failed",
                extra={
                    "key": self._key,
                    "error_type": type(exc).__name__,
                    "fail_open": self._fail_open,
                },
            )
            self._acquired = self._fail_open
            return self._fail_open
        self._acquired = bool(acquired)
        return self._acquired

    async def release(self) -> None:
        """Nhả khoá bằng Lua compare-and-delete."""
        if self._client is None or not self._acquired:
            self._acquired = False
            return
        self._acquired = False
        try:
            await self._client.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
        except Exception as exc:  # noqa: BLE001 - TTL sẽ tự dọn khoá
            logger.warning(
                "worker_lock_release_failed",
                extra={"key": self._key, "error_type": type(exc).__name__},
            )

    async def extend(self) -> bool:
        """Gia hạn TTL bằng Lua compare-and-expire.

        Returns:
            True khi vẫn sở hữu khoá và gia hạn thành công.
        """
        if self._client is None:
            return True
        if not self._acquired:
            return False
        try:
            extended = await self._client.eval(
                _EXTEND_SCRIPT, 1, self._key, self._token, self._ttl_seconds * 1000
            )
        except Exception as exc:  # noqa: BLE001 - engine sẽ thử lại ở chu kỳ sau
            logger.warning(
                "worker_lock_extend_failed",
                extra={"key": self._key, "error_type": type(exc).__name__},
            )
            return False
        return bool(extended)


def build_lock(
    client: Redis | None,
    key: str,
    ttl_seconds: int,
    *,
    token: str | None = None,
    fail_open: bool = True,
) -> DistributedLock:
    """Factory lock mặc định của engine.

    Args:
        client: Redis client hoặc None.
        key: Tên khoá.
        ttl_seconds: TTL giây.
        token: Token sở hữu tuỳ chọn.
        fail_open: Cho chạy tiếp khi Redis lỗi.

    Returns:
        `NullLock` khi Redis tắt, ngược lại `RedisLock`.
    """
    if client is None:
        return NullLock()
    return RedisLock(client, key, ttl_seconds=ttl_seconds, token=token, fail_open=fail_open)
