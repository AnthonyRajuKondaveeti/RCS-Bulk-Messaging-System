"""
Dispatcher Worker Entry Point

Runs ONLY the MessageDispatcher worker — no other workers.

Concurrency is configurable via the DISPATCHER_CONCURRENCY env var (default 10).
Run multiple replicas of this container to scale message throughput horizontally.

Usage:
    python -m apps.workers.entrypoints.dispatcher

Docker:
    command: ["python", "-m", "apps.workers.entrypoints.dispatcher"]
    environment:
      DISPATCHER_CONCURRENCY: "20"
"""

import asyncio
import os
import signal

from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker-dispatcher")

import structlog
from apps.workers.dispatcher.message_dispatcher import MessageDispatcher

logger = structlog.get_logger(__name__)

CONCURRENCY = int(os.getenv("DISPATCHER_CONCURRENCY", "10"))


async def main() -> None:
    worker = MessageDispatcher(concurrency=CONCURRENCY)

    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("shutdown_signal_received", worker="dispatcher")
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    logger.info("worker_starting", worker="dispatcher", concurrency=CONCURRENCY)
    await worker.start()
    logger.info("worker_stopped", worker="dispatcher")


if __name__ == "__main__":
    asyncio.run(main())
