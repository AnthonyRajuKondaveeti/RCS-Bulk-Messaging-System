"""Webhook Processor Worker

Consumes webhook.process queue and calls DeliveryService.handle_delivery_status_update().
Uses AggregatorFactory which returns RcsSmsAdapter (or MockAdapter) based on settings.
"""

import asyncio
import logging
import time
from typing import Dict, Any

from apps.core.services.delivery_service import DeliveryService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.ports.aggregator import WebhookValidationException
from apps.core.config import get_settings


import structlog

logger = structlog.get_logger(__name__)



class WebhookProcessor:
    """
    Webhook Processor Worker.

    Pulls jobs from webhook.process queue.  Each job was enqueued by the
    /api/v1/webhooks/rcssms API route after receiving a DLR push from rcssms.in.

    The aggregator is used only to parse the raw payload into a DeliveryStatus.
    """

    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        concurrency: int = 20,
    ):
        self.db = db or get_database()
        self.queue = queue
        self.concurrency = concurrency
        self.settings = get_settings()
        self.aggregator = None
        self.running = False

    async def start(self) -> None:
        """Start the webhook processor worker."""
        logger.info("🚀 Webhook Processor starting...")

        await self.db.connect()

        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=self.concurrency,
            )
        await self.queue.connect()

        # Use factory — returns RcsSmsAdapter or MockAdapter
        from apps.core.aggregators.factory import AggregatorFactory
        self.aggregator = AggregatorFactory.create_aggregator(self.settings)

        self.running = True

        logger.info("✅ Webhook Processor ready",
                    extra={
                        "queue": self.settings.queue_names["webhook_processor"],
                        "concurrency": self.concurrency,
                        "aggregator": self.aggregator.get_name(),
                    })

        await self.queue.subscribe(
            queue_name=self.settings.queue_names["webhook_processor"],
            handler=self.process_webhook_job,
            prefetch=self.concurrency,
        )

    async def stop(self) -> None:
        logger.info("🛑 Webhook Processor stopping...")
        self.running = False
        if self.aggregator:
            await self.aggregator.close()
        if self.queue:
            await self.queue.close()
        await self.db.disconnect()

    async def process_webhook_job(self, job: QueueJob) -> None:
        """
        Process a single DLR webhook job.

        Args:
            job: payload must contain:
                 {"webhook_id": str, "payload": dict, "headers": dict}
        """
        webhook_id = job.payload.get("webhook_id", "unknown")
        start = time.monotonic()

        logger.info("📬 Webhook job received",
                    extra={"webhook_id": webhook_id, "attempt": job.attempt})

        try:
            payload = job.payload.get("payload", {})
            headers = job.payload.get("headers", {})

            # Parse DLR through adapter
            try:
                delivery_status = await self.aggregator.handle_webhook(
                    payload=payload,
                    headers=headers,
                )
            except WebhookValidationException as e:
                logger.error("Invalid webhook signature — dropping",
                             extra={"webhook_id": webhook_id, "error": str(e)})
                return  # Do NOT requeue invalid webhooks

            if not delivery_status:
                logger.warning("Could not parse webhook payload — dropping",
                               extra={"webhook_id": webhook_id, "payload": str(payload)})
                return

            logger.info("DLR parsed",
                        extra={
                            "webhook_id": webhook_id,
                            "external_id": delivery_status.external_id,
                            "status": delivery_status.status,
                        })

            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                async with uow:
                    service = DeliveryService(
                        uow=uow,
                        aggregator=self.aggregator,
                        queue=self.queue,
                    )
                    await service.handle_delivery_status_update(
                        external_id=delivery_status.external_id,
                        status=delivery_status.status,
                        error_code=delivery_status.error_code,
                        error_message=delivery_status.error_message,
                    )

            elapsed = round(time.monotonic() - start, 3)
            logger.info("✅ Webhook job done",
                        extra={
                            "webhook_id": webhook_id,
                            "external_id": delivery_status.external_id,
                            "final_status": delivery_status.status,
                            "elapsed_seconds": elapsed,
                        })

        except Exception:
            elapsed = round(time.monotonic() - start, 3)
            logger.exception("❌ Webhook job failed",
                             extra={"webhook_id": webhook_id, "elapsed_seconds": elapsed})
            raise


async def main():
    processor = WebhookProcessor()
    try:
        await processor.start()
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await processor.stop()


if __name__ == "__main__":
    asyncio.run(main())
