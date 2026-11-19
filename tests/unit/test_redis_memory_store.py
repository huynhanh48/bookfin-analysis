"""Management mirror does not leak outcomes into decision evidence."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from redis.exceptions import ConnectionError

from app.adapters.redis_memory_store import RedisMemoryStore


def client_with_pipeline():
    """Create a mock transaction whose command methods match redis-py semantics."""
    client = MagicMock()
    pipeline = MagicMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)
    pipeline.execute = AsyncMock()
    client.pipeline.return_value = pipeline
    return client, pipeline


async def test_document_mirror_has_original_text_and_no_expiry():
    client, pipeline = client_with_pipeline()
    store = RedisMemoryStore(client)
    job = SimpleNamespace(
        id=uuid4(),
        namespace="knowledge",
        case_id=None,
        kind="text",
        source="notes",
        text="Luận điểm breakout",
        file_key=None,
        status="PENDING",
        error_code=None,
        attempts=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await store.document(job)
    key = pipeline.hset.call_args.args[0]
    assert key == f"stock:rag:documents:{job.id}"
    assert json.loads(pipeline.hset.call_args.kwargs["mapping"]["text"]) == job.text
    pipeline.expire.assert_not_called()
    pipeline.execute.assert_awaited_once()


async def test_future_outcome_is_in_a_separate_hash():
    client, pipeline = client_with_pipeline()
    store = RedisMemoryStore(client)
    case = SimpleNamespace(
        id=uuid4(),
        symbol="FPT",
        timeframe="D1",
        analyzed_at=datetime.now(UTC),
        thesis="HH HL breakout",
        indicator_snapshot={},
        market_context={},
        conclusion="",
        source="test",
        chart_path=None,
        ingest_status="COMPLETED",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        outcome={"5_sessions": 2.1},
    )
    assert await store.case(case)
    calls = pipeline.hset.call_args_list
    assert "outcome" not in calls[0].kwargs["mapping"]
    assert calls[1].args[0] == f"stock:analysis:outcomes:{case.id}"
    assert json.loads(calls[1].kwargs["mapping"]["outcome"]) == case.outcome


async def test_redis_outage_is_best_effort_and_sanitized():
    client, pipeline = client_with_pipeline()
    pipeline.execute.side_effect = ConnectionError("private-provider-details")
    assert not await RedisMemoryStore(client)._write("stock:test", {"text": "safe"})
