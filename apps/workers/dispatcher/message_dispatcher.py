"""
Message Dispatcher Worker

Consumes message.dispatch queue and calls DeliveryService per message.
Uses AggregatorFactory which returns RcsSmsAdapter (or MockAdapter) based
on settings.
"""

import asyncio
import logging
import time
from uuid import UUID

from apps.core.domain.message import MessageStatus
from apps.core.services.delivery_service import DeliveryService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.config import get_settings


import structlog

logger = structlog.get_logger(__name__)



class MessageDispatcher:
    """
    Message Dispatcher Worker.

    Pulls jobs from message.dispatch queue; for each job loads the message
    from DB and calls DeliveryService.process_message_delivery().

    Concurrency is controlled by RabbitMQ prefetch_count — each asyncio task
    processes one message at a time, but N tasks run concurrently.
    """

    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        concurrency: int = 10,
    ):
        self.db = db or get_database()
        self.queue = queue
        self.concurrency = concurrency
        self.settings = get_settings()
        self.aggregator = None
        self.running = False

    async def start(self) -> None:
        """Start the dispatcher worker."""
        logger.info(
            "🚀 Message Dispatcher starting...",
            extra={"concurrency": self.concurrency},
        )

        await self.db.connect()

        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=self.concurrency,
            )
        await self.queue.connect()

        from apps.core.aggregators.factory import AggregatorFactory

        self.aggregator = AggregatorFactory.create_aggregator(self.settings)

        self.running = True

        logger.info(
            "✅ Message Dispatcher ready",
            extra={
                "queue": self.settings.queue_names["message_dispatcher"],
                "concurrency": self.concurrency,
                "aggregator": self.aggregator.get_name(),
            },
        )

        await self.queue.subscribe(
            queue_name=self.settings.queue_names["message_dispatcher"],
            handler=self.process_message_job,
            prefetch=self.concurrency,
        )

    async def stop(self) -> None:
        """Stop the dispatcher worker."""
        logger.info("🛑 Message Dispatcher stopping...")
        self.running = False
        if self.aggregator:
            await self.aggregator.close()
        if self.queue:
            await self.queue.close()
        await self.db.disconnect()

    async def process_message_job(self, job: QueueJob) -> None:
        """
        Process a single message delivery job.

        Args:
            job: Queue job payload must contain {"message_id": "<uuid>"}
        """
        message_id = UUID(job.payload["message_id"])
        start = time.monotonic()

        logger.info(
            "📨 Message job received",
            extra={"message_id": str(message_id), "attempt": job.attempt},
        )

        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                async with uow:
                    # Idempotency guard — RabbitMQ is at-least-once.
                    # If the dispatcher crashes after sending but before ACKing,
                    # the broker redelivers the same message_id.  Check current
                    # status so we never send the same message twice.
                    message = await uow.messages.get_by_id(message_id)
                    if message is None:
                        logger.warning(
                            "Message not found — skipping (may have been deleted)",
                            extra={"message_id": str(message_id)},
                        )
                        return  # ACK — don't requeue

                    if message.status not in (
                        MessageStatus.PENDING,
                        MessageStatus.QUEUED,
                    ):
                        logger.info(
                            "Message already processed (status=%s) — idempotency skip",
                            message.status.value,
                            extra={"message_id": str(message_id)},
                        )
                        return  # ACK without re-sending

                    service = DeliveryService(
                        uow=uow,
                        aggregator=self.aggregator,
                        queue=self.queue,
                    )
                    await service.process_message_delivery(message_id)

            elapsed = round(time.monotonic() - start, 3)
            logger.info(
                "✅ Message job done",
                extra={"message_id": str(message_id), "elapsed_seconds": elapsed},
            )

        except Exception:
            elapsed = round(time.monotonic() - start, 3)
            logger.exception(
                "❌ Message job failed",
                extra={"message_id": str(message_id), "elapsed_seconds": elapsed},
            )
            # Re-raise so RabbitMQ requeues (at-least-once delivery)
            raise


async def main():
    dispatcher = MessageDispatcher()
    try:
        await dispatcher.start()
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await dispatcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
