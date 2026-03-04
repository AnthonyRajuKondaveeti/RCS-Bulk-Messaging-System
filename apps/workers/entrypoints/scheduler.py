"""
Scheduler Worker Entry Point

Runs the scheduled campaign poller.

Usage:
    python -m apps.workers.entrypoints.scheduler

Docker:
    command: ["python", "-m", "apps.workers.entrypoints.scheduler"]
"""

import asyncio
import os
import signal

from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker-scheduler")

import structlog
from apps.workers.scheduler import ScheduledCampaignPoller

logger = structlog.get_logger(__name__)

POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "60"))


async def main() -> None:
    worker = ScheduledCampaignPoller(poll_interval=POLL_INTERVAL)

    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("shutdown_signal_received", worker="scheduler")
        asyncio.create_task(worker.stop())

    # loop.add_signal_handler is Unix-only; gracefully skip on Windows
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)
    except NotImplementedError:
        pass

    logger.info("worker_starting", worker="scheduler", poll_interval=POLL_INTERVAL)
    await worker.start()
    logger.info("worker_stopped", worker="scheduler")


if __name__ == "__main__":
    asyncio.run(main())
