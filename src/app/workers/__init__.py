"""Worker engine thống nhất: registry, executor và các role.

Entrypoint khuyến nghị::

    python -m app.workers list
    python -m app.workers run <job>
    python -m app.workers scheduler
    python -m app.workers realtime

Các module cũ (`app.workers.scheduler`, `indicator_worker`, `divergence_scan`,
`realtime`, `backfill`, `rag_worker`) vẫn được giữ để tương thích ngược.
"""

from app.workers.engine import JobOutcome, JobStatus, WorkerEngine
from app.workers.registry import (
    JobContext,
    JobRegistry,
    JobResult,
    JobSpec,
    discover_jobs,
    get_registry,
    register_job,
)

__all__ = [
    "JobContext",
    "JobOutcome",
    "JobRegistry",
    "JobResult",
    "JobSpec",
    "JobStatus",
    "WorkerEngine",
    "discover_jobs",
    "get_registry",
    "register_job",
]
