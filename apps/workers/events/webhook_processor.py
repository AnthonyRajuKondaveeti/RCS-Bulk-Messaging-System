"""
Webhook Processor Worker

Processes delivery status webhooks from aggregators asynchronously.
Updates message and campaign statistics based on delivery events.

Responsibilities:
    - Consume webhook events from queue
    - Parse aggregator-specific payload
    - Update message status
    - Update campaign statistics
    - Trigger fallback if needed
    - Log delivery events

Flow:
    1. Receive webhook event from queue
    2. Verify webhook signature
    3. Parse delivery status
    4. Update message in database
    5. Update campaign statistics
    6. Trigger fallback if delivery failed

Webhook Event Types:
    - sent: Message sent to carrier
    - delivered: Message delivered to device
    - read: Message read by recipient (RCS only)
    - failed: Delivery failed
"""

import asyncio
import logging
from typing import Dict, Any
from uuid import UUID

from apps.core.services.delivery_service import DeliveryService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.aggregators.gupshup_adapter import GupshupAdapter
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.ports.aggregator import WebhookValidationException
from apps.core.config import get_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WebhookProcessor:
    """
    Webhook Processor Worker
    
    Handles delivery status webhooks from aggregators asynchronously.
    
    Example:
        >>> processor = WebhookProcessor()
        >>> await processor.start()
    """
    
    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        aggregator: GupshupAdapter = None,
        concurrency: int = 20,
    ):
        """
        Initialize webhook processor
        
        Args:
            db: Database instance
            queue: Message queue
            aggregator: Aggregator for webhook parsing
            concurrency: Number of concurrent workers
        """
        self.db = db or get_database()
        self.queue = queue
        self.aggregator = aggregator
        self.concurrency = concurrency
        self.settings = get_settings()
        self.running = False
    
    async def start(self) -> None:
        """Start the webhook processor worker"""
        logger.info("🚀 Webhook Processor starting...")
        
        # Connect to database
        await self.db.connect()
        
        # Connect to queue
        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=self.concurrency,
            )
        await self.queue.connect()
        
        # Initialize aggregator for webhook parsing
        if not self.aggregator:
            from apps.core.aggregators.factory import AggregatorFactory
            self.aggregator = AggregatorFactory.create_aggregator(self.settings)
        
        self.running = True
        
        # Subscribe to webhook queue
        await self.queue.subscribe(
            queue_name=self.settings.queue_names["webhook_processor"],
            handler=self.process_webhook_job,
            prefetch=self.concurrency,
        )
        
        logger.info(
            f"✅ Webhook Processor ready "
            f"(concurrency={self.concurrency})"
        )
    
    async def stop(self) -> None:
        """Stop the webhook processor worker"""
        logger.info("🛑 Webhook Processor stopping...")
        self.running = False
        
        if self.aggregator:
            await self.aggregator.close()
        
        await self.queue.close()
        await self.db.disconnect()
    
    async def process_webhook_job(self, job: QueueJob) -> None:
        """
        Process a webhook event job
        
        Args:
            job: Queue job with webhook payload
        """
        webhook_id = job.payload.get("webhook_id", "unknown")
        
        logger.info(f"📬 Processing webhook {webhook_id}")
        
        try:
            # Extract webhook data
            payload = job.payload.get("payload", {})
            headers = job.payload.get("headers", {})
            
            # Parse webhook through aggregator
            try:
                delivery_status = await self.aggregator.handle_webhook(
                    payload=payload,
                    headers=headers,
                )
            except WebhookValidationException as e:
                logger.error(f"Invalid webhook signature: {e}")
                # Don't requeue - invalid webhooks should be dropped
                return
            
            if not delivery_status:
                logger.warning(f"Could not parse webhook {webhook_id}")
                return
            
            # Update message status
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                
                async with uow:
                    delivery_service = DeliveryService(
                        uow=uow,
                        aggregator=self.aggregator,
                        queue=self.queue,
                    )
                    
                    await delivery_service.handle_delivery_status_update(
                        external_id=delivery_status.external_id,
                        status=delivery_status.status,
                        error_code=delivery_status.error_code,
                        error_message=delivery_status.error_message,
                    )
                
                logger.info(
                    f"✅ Webhook {webhook_id} processed - "
                    f"Message {delivery_status.external_id} -> {delivery_status.status}"
                )
                
        except Exception as e:
            logger.exception(f"❌ Error processing webhook {webhook_id}")
            raise


async def main():
    """Main entry point"""
    processor = WebhookProcessor()
    
    try:
        await processor.start()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await processor.stop()


if __name__ == "__main__":
    asyncio.run(main())
