"""
Orchestrator Worker Entry Point

Runs ONLY the CampaignOrchestrator — no other workers.

Usage:
    python -m apps.workers.entrypoints.orchestrator

Docker:
    command: ["python", "-m", "apps.workers.entrypoints.orchestrator"]
"""

import asyncio
import os
import signal

from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker-orchestrator")

import structlog
from apps.workers.orchestrator.campaign_orchestrator import CampaignOrchestrator

logger = structlog.get_logger(__name__)

BATCH_SIZE = int(os.getenv("ORCHESTRATOR_BATCH_SIZE", "1000"))


async def main() -> None:
    worker = CampaignOrchestrator(batch_size=BATCH_SIZE)

    loop = asyncio.get_running_loop()

    def _stop():
        logger.info("shutdown_signal_received", worker="orchestrator")
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    logger.info("worker_starting", worker="orchestrator", batch_size=BATCH_SIZE)
    await worker.start()
    logger.info("worker_stopped", worker="orchestrator")


if __name__ == "__main__":
    asyncio.run(main())
