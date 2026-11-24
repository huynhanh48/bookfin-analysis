"""Shutdown steps must stay bounded so uvicorn can reload or exit."""

import asyncio
import time

from app.main import _shutdown_step


async def test_stuck_step_is_abandoned_after_timeout():
    started = time.monotonic()
    await _shutdown_step("stuck", asyncio.sleep(60), 0.05)
    assert time.monotonic() - started < 1


async def test_step_failure_does_not_propagate():
    async def fail():
        raise RuntimeError("provider did not close")

    await _shutdown_step("failing", fail(), 1)
