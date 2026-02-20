"""
Message Dispatcher Worker

Consumes message queue and sends messages via aggregators.
Handles RCS capability checking, retries, and fallback triggers.

Responsibilities:
    - Consume message queue
    - Check RCS capability
    - Send via appropriate aggregator
    - Handle delivery failures
    - Trigger retries or fallback
    - Update message and campaign statistics

Flow:
    1. Receive message from queue
    2. Load message from database
    3. Check RCS capability
    4. Send via Gupshup adapter
    5. Update message status
    6. Handle failures (retry or fallback)
"""

import asyncio
import logging
from uuid import UUID

from apps.core.domain.message import MessageStatus, MessageChannel, FailureReason
from apps.core.services.delivery_service import DeliveryService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.aggregators.gupshup_adapter import GupshupAdapter
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.config import get_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MessageDispatcher:
    """
    Message Dispatcher Worker
    
    Sends messages via RCS/SMS aggregators with retry and fallback.
    
    Example:
        >>> dispatcher = MessageDispatcher()
        >>> await dispatcher.start()
    """
    
    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        aggregator: GupshupAdapter = None,
        concurrency: int = 10,
    ):
        """
        Initialize dispatcher
        
        Args:
            db: Database instance
            queue: Message queue
            aggregator: RCS/SMS aggregator
            concurrency: Number of concurrent workers
        """
        self.db = db or get_database()
        self.queue = queue
        self.aggregator = aggregator
        self.concurrency = concurrency
        self.settings = get_settings()
        self.running = False
    
    async def start(self) -> None:
        """Start the dispatcher worker"""
        logger.info("🚀 Message Dispatcher starting...")
        
        # Connect to database
        await self.db.connect()
        
        # Connect to queue
        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=self.concurrency,
            )
        await self.queue.connect()
        
        # Initialize aggregator
        if not self.aggregator:
            from apps.core.aggregators.factory import AggregatorFactory
            self.aggregator = AggregatorFactory.create_aggregator(self.settings)
        
        self.running = True
        
        # Subscribe to message queue
        await self.queue.subscribe(
            queue_name=self.settings.queue_names["message_dispatcher"],
            handler=self.process_message_job,
            prefetch=self.concurrency,
        )
        
        logger.info(
            f"✅ Message Dispatcher ready "
            f"(concurrency={self.concurrency})"
        )
    
    async def stop(self) -> None:
        """Stop the dispatcher worker"""
        logger.info("🛑 Message Dispatcher stopping...")
        self.running = False
        
        if self.aggregator:
            await self.aggregator.close()
        
        await self.queue.close()
        await self.db.disconnect()
    
    async def process_message_job(self, job: QueueJob) -> None:
        """
        Process a message delivery job
        
        Args:
            job: Queue job with message_id
        """
        message_id = UUID(job.payload["message_id"])
        
        logger.info(f"📨 Processing message {message_id}")
        
        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                
                async with uow:
                    # Create delivery service
                    delivery_service = DeliveryService(
                        uow=uow,
                        aggregator=self.aggregator,
                        queue=self.queue,
                    )
                    
                    # Process delivery
                    await delivery_service.process_message_delivery(message_id)
                
                logger.info(f"✅ Message {message_id} processed")
                
        except Exception as e:
            logger.exception(f"❌ Error processing message {message_id}")
            
            # Message will be requeued automatically by RabbitMQ
            # if not acknowledged (which happens on exception)
            raise


async def main():
    """Main entry point"""
    dispatcher = MessageDispatcher()
    
    try:
        await dispatcher.start()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await dispatcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
