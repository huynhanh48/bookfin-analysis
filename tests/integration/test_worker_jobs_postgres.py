"""Integration: job đọc dữ liệu thật qua registry và PostgreSQL."""

import pytest

from app.core.config import get_settings
from app.workers.engine import JobStatus, WorkerEngine
from app.workers.registry import discover_jobs

pytestmark = pytest.mark.integration


async def test_divergence_scan_job_runs_against_database():
    """Job quét phân kỳ chỉ đọc, xác nhận engine + registry + repository khớp nhau."""
    settings = get_settings()
    engine = WorkerEngine(settings, discover_jobs(), redis=None)

    outcome = await engine.execute("divergence-scan", {"limit": 5, "days": 1})

    assert outcome.status is JobStatus.SUCCEEDED, outcome.as_dict()
    assert "signals" in outcome.summary
    assert "symbols_with_signals" in outcome.summary
