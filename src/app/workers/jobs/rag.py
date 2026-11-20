"""Job xử lý hàng đợi RAG durable.

RAG runtime giữ file lock độc quyền trên volume, nên job này chỉ dùng khi tiến
trình API không sở hữu consumer RAG. Nếu runtime đang bận, job trả `skipped`
thay vì làm sập worker.
"""

from __future__ import annotations

import contextlib

from pydantic import Field

from app.core.logging import get_logger
from app.workers.registry import JobContext, JobParams, JobResult, RetryPolicy, register_job

logger = get_logger(__name__)


class RagIngestionParams(JobParams):
    """Tham số drain hàng đợi RAG.

    Attributes:
        max_jobs: Số job tối đa xử lý trong một lần chạy để giữ timeout bounded.
    """

    max_jobs: int = Field(default=3, ge=1, le=100)


@register_job(
    name="rag-ingestion",
    params_model=RagIngestionParams,
    description="Drain hàng đợi RAG durable trong một batch có giới hạn.",
    schedule=None,
    timeout_seconds=1800,
    retry=RetryPolicy(attempts=2, base_delay_seconds=5, max_delay_seconds=60, jitter_seconds=1),
    idempotency="none",
)
async def rag_ingestion(ctx: JobContext[RagIngestionParams]) -> JobResult:
    """Xử lý tối đa `max_jobs` job RAG đang chờ.

    Args:
        ctx: Ngữ cảnh job.

    Returns:
        `JobResult` với số job đã xử lý; `skipped` khi RAG tắt hoặc runtime bận.
    """
    settings = ctx.settings
    if not settings.rag_enabled:
        return JobResult(skipped=True, reason="rag_disabled", summary={"processed": 0})

    from app.core.rag_runtime import RagRuntime, RagUnavailableError
    from app.workers.rag_worker import process_pending, validate_runtime

    try:
        runtime = RagRuntime(settings)
    except RagUnavailableError:
        logger.info("rag_ingestion_runtime_busy")
        return JobResult(
            skipped=True,
            reason="rag_runtime_busy",
            summary={"processed": 0},
        )

    try:
        await validate_runtime(runtime)
        processed = await process_pending(runtime, max_jobs=ctx.params.max_jobs)
    finally:
        with contextlib.suppress(Exception):
            await runtime.close()

    logger.info("job_rag_ingestion_done", extra={"processed": processed})
    return JobResult(summary={"processed": processed})
