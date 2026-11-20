"""Integration: Redis lock/token safety và idempotency marker thật."""

import asyncio
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis

from app.core.config import Settings
from app.workers.engine import JobStatus, WorkerEngine
from app.workers.locks import RedisLock
from app.workers.registry import EmptyParams, JobContext, JobRegistry, JobResult, JobSpec

pytestmark = pytest.mark.integration
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


async def redis_client_or_skip() -> Redis:
    """Kết nối Redis từ `.env`; skip khi dịch vụ không chạy."""
    base = Settings()
    client = Redis.from_url(
        base.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=3,
    )
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis không chạy hoặc không truy cập được")
    return client


async def test_redis_lock_is_exclusive_and_release_is_token_safe():
    client = await redis_client_or_skip()
    key = f"worker:test:lock:{uuid4().hex}"
    first = RedisLock(client, key, ttl_seconds=10, token="token-a")
    second = RedisLock(client, key, ttl_seconds=10, token="token-b")
    try:
        assert await first.acquire() is True
        assert await second.acquire() is False

        assert await first.extend() is True
        await first.release()
        assert await client.exists(key) == 0
    finally:
        await client.delete(key)
        await client.aclose()


async def test_expired_owner_cannot_delete_new_owner_lock():
    client = await redis_client_or_skip()
    key = f"worker:test:lock:{uuid4().hex}"
    old_owner = RedisLock(client, key, ttl_seconds=1, token="old")
    new_owner = RedisLock(client, key, ttl_seconds=10, token="new")
    try:
        assert await old_owner.acquire() is True
        # Ép khoá hết hạn thay vì sleep 1 giây để test không flaky dưới tải.
        assert await client.pexpire(key, 50) is True
        await asyncio.sleep(0.1)
        assert await new_owner.acquire() is True

        # Old owner hết TTL không được xoá khoá của new owner.
        await old_owner.release()
        assert await client.exists(key) == 1

        assert await new_owner.extend() is True
        await new_owner.release()
        assert await client.exists(key) == 0
    finally:
        await client.delete(key)
        await client.aclose()


async def test_engine_idempotency_marker_uses_real_redis():
    client = await redis_client_or_skip()
    job_name = f"test-idem-{uuid4().hex[:12]}"
    calls = 0

    async def handler(ctx: JobContext[EmptyParams]) -> JobResult:
        nonlocal calls
        calls += 1
        return JobResult(summary={"calls": calls})

    registry = JobRegistry()
    registry.register(
        JobSpec(
            name=job_name,
            handler=handler,
            params_model=EmptyParams,
            idempotency="day",
        )
    )
    settings = Settings(_env_file=None, app_env="test")
    engine = WorkerEngine(
        settings,
        registry,
        redis=client,
        now=lambda: datetime(2026, 11, 20, 16, 0, tzinfo=TZ),
    )
    try:
        first = await engine.execute(job_name, trigger="scheduler")
        second = await engine.execute(job_name, trigger="scheduler")
        manual = await engine.execute(job_name, trigger="manual")

        assert first.status is JobStatus.SUCCEEDED
        assert second.status is JobStatus.SKIPPED_DUPLICATE
        assert manual.status is JobStatus.SUCCEEDED
        assert calls == 2
    finally:
        keys = [key async for key in client.scan_iter(match=f"worker:*:{job_name}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
