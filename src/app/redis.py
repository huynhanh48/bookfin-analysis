"""Async Redis client factory.

Redis backs caching, Redis Streams and the background job queue. It is optional
at initialization stage: the API runs without a Redis server when
`REDIS_ENABLED` is false.
"""

from redis.asyncio import Redis

from app.config import get_settings


def create_redis_client() -> Redis | None:
    """Create an async Redis client, or return None when Redis is disabled."""
    settings = get_settings()
    if not settings.redis_enabled:
        return None
    return Redis.from_url(settings.redis_url, decode_responses=True)
