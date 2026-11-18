"""Redis client tuỳ chọn.

Redis chỉ phục vụ cache nóng và fan-out realtime; lịch sử nến luôn nằm ở
PostgreSQL. Khi `REDIS_ENABLED=false` ứng dụng vẫn chạy đầy đủ, nên module này
không tạo kết nối ở thời điểm import.
"""

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis_client() -> Redis | None:
    """Trả về Redis client dùng chung, hoặc `None` khi Redis bị tắt.

    Returns:
        Client `redis.asyncio` giải mã UTF-8 khi `REDIS_ENABLED=true`, ngược lại
        trả `None` để tầng trên chủ động bỏ qua cache.
    """
    global _client
    settings = get_settings()
    if not settings.redis_enabled:
        return None
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis_client() -> None:
    """Đóng kết nối Redis nếu đã được tạo."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
