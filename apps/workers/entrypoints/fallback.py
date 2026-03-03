"""
SMS Fallback Worker Entry Point

Runs ONLY the SMSFallbackWorker — no other workers.

Concurrency is configurable via FALLBACK_CONCURRENCY (default 5).

Usage:
    python -m apps.workers.entrypoints.fallback

Docker:
    command: ["python", "-m", "apps.workers.entrypoints.fallback"]
    environment:
      FALLBACK_CONCURRENCY: "10"
"""

import asyncio
import os
import signal

from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker-fallback")

import structlog
from apps.workers.fallback.sms_fallback_worker import SMSFallbackWorker

logger = structlog.get_logger(__name__)

CONCURRENCY = int(os.getenv("FALLBACK_CONCURRENCY", "5"))


async def main() -> None:
    worker = SMSFallbackWorker(concurrency=CONCURRENCY)

    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("shutdown_signal_received", worker="fallback")
        asyncio.create_task(worker.stop())

    # loop.add_signal_handler is Unix-only; gracefully skip on Windows
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)
    except NotImplementedError:
        pass

    logger.info("worker_starting", worker="fallback", concurrency=CONCURRENCY)
    await worker.start()
    logger.info("worker_stopped", worker="fallback")


if __name__ == "__main__":
    asyncio.run(main())
