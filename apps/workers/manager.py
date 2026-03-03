"""
Worker Manager — All-in-one development runner

FIX (GAP 14): Old implementation used asyncio.gather() to start all workers.
One worker crashing cancelled all others — zero fault isolation.

New approach: each worker runs as an independent asyncio.Task.  If one
crashes, the manager logs the error, waits a backoff period, and restarts
that worker individually without touching the others.

NOTE — Production deployment:
    In production (docker-compose.prod.yml), each worker type runs as its
    OWN service with its own entry point (apps/workers/entrypoints/).
    This all-in-one manager is convenient for local development only.

Usage (local dev):
    python -m apps.workers.manager

Production:
    docker compose -f docker-compose.prod.yml up -d
    # → runs worker-orchestrator, worker-dispatcher, worker-webhook,
    #     worker-fallback as separate scalable services
"""

import asyncio
import logging
import signal
from typing import Dict, Optional

from apps.workers.orchestrator.campaign_orchestrator import CampaignOrchestrator
from apps.workers.dispatcher.message_dispatcher import MessageDispatcher
from apps.workers.events.webhook_processor import WebhookProcessor
from apps.workers.fallback.sms_fallback_worker import SMSFallbackWorker


from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker")

import structlog
logger = structlog.get_logger(__name__)



import os

WORKER_CONFIGS = {
    "orchestrator": {
        "cls": CampaignOrchestrator,
        "kwargs": {
            "batch_size": int(os.getenv("ORCHESTRATOR_BATCH_SIZE", "1000")),
        },
        "description": "Campaign Orchestrator",
    },
    "dispatcher": {
        "cls": MessageDispatcher,
        "kwargs": {
            "concurrency": int(os.getenv("DISPATCHER_CONCURRENCY", "10")),
        },
        "description": "Message Dispatcher",
    },
    "webhook_processor": {
        "cls": WebhookProcessor,
        "kwargs": {
            "concurrency": int(os.getenv("WEBHOOK_CONCURRENCY", "20")),
        },
        "description": "Webhook Processor",
    },
    "fallback_worker": {
        "cls": SMSFallbackWorker,
        "kwargs": {
            "concurrency": int(os.getenv("FALLBACK_CONCURRENCY", "5")),
        },
        "description": "SMS Fallback Worker",
    },
}

RESTART_BACKOFF_SECONDS = 5
MAX_RESTARTS = 10  # per worker; after this the manager marks it permanently failed


class WorkerManager:
    """
    Manages multiple background workers with independent fault isolation.

    Each worker runs in its own asyncio.Task.  A crash in one worker triggers
    a supervised restart with exponential backoff; it does NOT affect any other
    worker.
    """

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._restart_counts: Dict[str, int] = {}
        self._shutdown = asyncio.Event()
        self.running = False

    async def start_all(self) -> None:
        """Start all workers as independent supervised tasks."""
        logger.info("🚀 Starting all workers with fault isolation...")

        for name, cfg in WORKER_CONFIGS.items():
            self._restart_counts[name] = 0
            task = asyncio.create_task(
                self._supervise(name, cfg),
                name=f"worker-{name}",
            )
            self._tasks[name] = task
            logger.info("  ✅ Started: %s", cfg["description"])

        self.running = True
        logger.info("All workers running (each isolated — one crash won't stop others)")

    async def _supervise(self, name: str, cfg: dict) -> None:
        """
        Supervised runner for a single worker.

        Restarts the worker on crash with exponential backoff up to MAX_RESTARTS.
        """
        backoff = RESTART_BACKOFF_SECONDS

        while not self._shutdown.is_set():
            worker = cfg["cls"](**cfg["kwargs"])
            try:
                logger.info("[%s] Starting...", name)
                await worker.start()
                # start() blocks until the worker finishes consuming; reaching
                # here means it exited cleanly.
                if not self._shutdown.is_set():
                    logger.warning("[%s] Exited cleanly but unexpectedly — restarting", name)
            except asyncio.CancelledError:
                logger.info("[%s] Cancelled — shutting down", name)
                return
            except Exception as exc:
                self._restart_counts[name] += 1
                restarts = self._restart_counts[name]

                if restarts > MAX_RESTARTS:
                    logger.critical(
                        "[%s] Exceeded max restarts (%d). Giving up. "
                        "Other workers are still running.",
                        name, MAX_RESTARTS,
                    )
                    return

                logger.error(
                    "[%s] Crashed (restart %d/%d): %s — restarting in %ds",
                    name, restarts, MAX_RESTARTS, exc, backoff,
                )
            finally:
                try:
                    await worker.stop()
                except Exception:
                    pass

            if self._shutdown.is_set():
                return

            # Wait before restarting, unless shutdown is requested
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=backoff,
                )
            except asyncio.TimeoutError:
                pass

            backoff = min(backoff * 2, 60)  # cap at 60s

    async def stop_all(self) -> None:
        """Signal all workers to stop and wait for them."""
        logger.info("🛑 Stopping all workers...")
        self._shutdown.set()
        self.running = False

        # Cancel all tasks
        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()

        # Wait for graceful shutdown
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        logger.info("✅ All workers stopped")


async def main():
    manager = WorkerManager()

    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("Received shutdown signal")
        asyncio.create_task(manager.stop_all())

    # loop.add_signal_handler is Unix-only; gracefully skip on Windows
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)
    except NotImplementedError:
        # Windows: fall back to KeyboardInterrupt handling below
        pass

    try:
        await manager.start_all()
        # Block until shutdown
        while manager.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    except Exception:
        logger.exception("Worker manager error")
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
