"""
Webhook Processor Worker Entry Point

Runs ONLY the WebhookProcessor — no other workers.

Concurrency is configurable via WEBHOOK_CONCURRENCY (default 20).

Usage:
    python -m apps.workers.entrypoints.webhook

Docker:
    command: ["python", "-m", "apps.workers.entrypoints.webhook"]
    environment:
      WEBHOOK_CONCURRENCY: "40"
"""

import asyncio
import os
import signal

from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker-webhook")

import structlog
from apps.workers.events.webhook_processor import WebhookProcessor

logger = structlog.get_logger(__name__)

CONCURRENCY = int(os.getenv("WEBHOOK_CONCURRENCY", "20"))


async def main() -> None:
    worker = WebhookProcessor(concurrency=CONCURRENCY)

    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("shutdown_signal_received", worker="webhook")
        asyncio.create_task(worker.stop())

    # loop.add_signal_handler is Unix-only; gracefully skip on Windows
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)
    except NotImplementedError:
        pass

    logger.info("worker_starting", worker="webhook", concurrency=CONCURRENCY)
    await worker.start()
    logger.info("worker_stopped", worker="webhook")


if __name__ == "__main__":
    asyncio.run(main())
