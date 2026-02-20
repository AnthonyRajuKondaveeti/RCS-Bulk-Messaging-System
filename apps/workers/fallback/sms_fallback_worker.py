"""
SMS Fallback Worker

Handles automatic SMS fallback when RCS delivery fails.
Converts RCS messages to SMS format and sends via SMS channel.

Responsibilities:
    - Monitor fallback queue
    - Load failed RCS messages
    - Convert rich content to plain SMS
    - Send via SMS channel
    - Update message status
    - Track fallback statistics

Flow:
    1. Receive fallback job from queue
    2. Load message from database
    3. Verify fallback should be triggered
    4. Convert RCS content to SMS text
    5. Send via SMS aggregator
    6. Update message and campaign stats

Fallback Triggers:
    - RCS not supported by recipient
    - Max retry attempts exceeded
    - Network failures (after retries)
"""

import asyncio
import logging
from uuid import UUID

from apps.core.domain.message import MessageStatus, MessageChannel
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.aggregators.gupshup_adapter import GupshupAdapter
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.ports.aggregator import SendMessageRequest, AggregatorException
from apps.core.config import get_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SMSFallbackWorker:
    """
    SMS Fallback Worker
    
    Automatically falls back to SMS when RCS delivery fails.
    
    Example:
        >>> worker = SMSFallbackWorker()
        >>> await worker.start()
    """
    
    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        aggregator: GupshupAdapter = None,
        concurrency: int = 10,
    ):
        """
        Initialize fallback worker
        
        Args:
            db: Database instance
            queue: Message queue
            aggregator: SMS aggregator
            concurrency: Number of concurrent workers
        """
        self.db = db or get_database()
        self.queue = queue
        self.aggregator = aggregator
        self.concurrency = concurrency
        self.settings = get_settings()
        self.running = False
    
    async def start(self) -> None:
        """Start the fallback worker"""
        logger.info("🚀 SMS Fallback Worker starting...")
        
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
            if self.settings.gupshup:
                self.aggregator = GupshupAdapter(
                    api_key=self.settings.gupshup.api_key,
                    app_name=self.settings.gupshup.app_name,
                    webhook_secret=self.settings.gupshup.webhook_secret,
                    base_url=self.settings.gupshup.base_url,
                )
            else:
                logger.error("No aggregator configured")
                return
        
        self.running = True
        
        # Subscribe to fallback queue
        await self.queue.subscribe(
            queue_name=self.settings.queue_names["fallback_handler"],
            handler=self.process_fallback_job,
            prefetch=self.concurrency,
        )
        
        logger.info(
            f"✅ SMS Fallback Worker ready "
            f"(concurrency={self.concurrency})"
        )
    
    async def stop(self) -> None:
        """Stop the fallback worker"""
        logger.info("🛑 SMS Fallback Worker stopping...")
        self.running = False
        
        if self.aggregator:
            await self.aggregator.close()
        
        await self.queue.close()
        await self.db.disconnect()
    
    async def process_fallback_job(self, job: QueueJob) -> None:
        """
        Process a fallback job
        
        Args:
            job: Queue job with message_id
        """
        message_id = UUID(job.payload["message_id"])
        
        logger.info(f"📲 Processing SMS fallback for message {message_id}")
        
        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                
                async with uow:
                    # Load message
                    message = await uow.messages.get_by_id(message_id)
                    if not message:
                        logger.error(f"Message {message_id} not found")
                        return
                    
                    # Verify fallback should be triggered
                    if not message.should_fallback_to_sms():
                        logger.warning(
                            f"Message {message_id} should not fallback "
                            f"(status={message.status}, "
                            f"fallback_triggered={message.fallback_triggered})"
                        )
                        return
                    
                    # Trigger fallback
                    message.trigger_fallback()
                    await uow.messages.save(message)
                    await uow.commit()  # Checkpoint before external call
                    
                    # Convert content to SMS
                    sms_text = message.content.to_sms_text()
                    
                    logger.info(
                        f"Converting to SMS: {message.content.text[:50]}... "
                        f"-> {sms_text[:50]}..."
                    )
                    
                    # Send via SMS
                    request = SendMessageRequest(
                        message_id=message.id,
                        recipient_phone=message.recipient_phone,
                        channel=MessageChannel.SMS,
                        content_text=sms_text,
                        priority=message.priority,
                    )
                    
                    try:
                        response = await self.aggregator.send_sms_message(request)
                        
                        if response.success:
                            # Update message as fallback sent
                            message.mark_fallback_sent(
                                aggregator=self.aggregator.get_name(),
                                external_id=response.external_id,
                            )
                            await uow.messages.save(message)
                            
                            # Update campaign stats
                            await uow.campaigns.update_stats(
                                message.campaign_id,
                                {"fallback_triggered": 1}
                            )
                            
                            # Implicit commit
                            
                            logger.info(
                                f"✅ SMS fallback sent for message {message_id} "
                                f"(external_id={response.external_id})"
                            )
                        else:
                            # SMS fallback also failed
                            logger.error(
                                f"SMS fallback failed for message {message_id}: "
                                f"{response.error_message}"
                            )
                            
                            # Update as failed
                            from apps.core.domain.message import FailureReason
                            message.mark_failed(
                                reason=FailureReason.NETWORK_ERROR,
                                error_code=response.error_code,
                                error_message=response.error_message,
                            )
                            await uow.messages.save(message)
                            
                            # Update campaign stats
                            await uow.campaigns.update_stats(
                                message.campaign_id,
                                {"messages_failed": 1}
                            )
                            
                            # Implicit commit
                            
                    except AggregatorException as e:
                        logger.error(f"SMS fallback error: {e}")
                        
                        # Mark as failed
                        from apps.core.domain.message import FailureReason
                        message.mark_failed(
                            reason=FailureReason.NETWORK_ERROR,
                            error_message=str(e),
                        )
                        await uow.messages.save(message)
                        # Implicit commit
                    
        except Exception as e:
            logger.exception(f"❌ Error processing fallback for {message_id}")
            raise


async def main():
    """Main entry point"""
    worker = SMSFallbackWorker()
    
    try:
        await worker.start()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
