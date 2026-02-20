"""
Campaign Orchestrator Worker

Executes campaigns by expanding audiences into individual messages
and queuing them for delivery.

Responsibilities:
    - Monitor scheduled campaigns
    - Load campaign and template data
    - Expand audience into messages
    - Apply rate limiting
    - Queue messages for dispatch
    - Update campaign status

Flow:
    1. Poll for scheduled/active campaigns
    2. Load template and render for each recipient
    3. Create Message entities
    4. Bulk save to database
    5. Queue messages for dispatcher
    6. Update campaign statistics
"""

import asyncio
import logging
from typing import List, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta

from apps.core.domain.campaign import Campaign, CampaignStatus
from apps.core.domain.message import Message, MessageContent
from apps.core.services.campaign_service import CampaignService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueMessage, QueuePriority, QueueJob
from apps.core.config import get_settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CampaignOrchestrator:
    """
    Campaign Orchestrator Worker
    
    Processes campaign execution by creating messages for all recipients
    and queuing them for delivery.
    
    Example:
        >>> orchestrator = CampaignOrchestrator()
        >>> await orchestrator.start()
    """
    
    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        batch_size: int = 100,
        poll_interval: int = 10,
    ):
        """
        Initialize orchestrator
        
        Args:
            db: Database instance
            queue: Message queue
            batch_size: Messages to create per batch
            poll_interval: Seconds between polls
        """
        self.db = db or get_database()
        self.queue = queue
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.settings = get_settings()
        self.running = False
    
    async def start(self) -> None:
        """Start the orchestrator worker"""
        logger.info("🚀 Campaign Orchestrator starting...")
        
        # Connect to database and queue
        await self.db.connect()
        
        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=1,  # Process one campaign at a time
            )
        await self.queue.connect()
        
        self.running = True
        
        # Subscribe to orchestrator queue
        await self.queue.subscribe(
            queue_name=self.settings.queue_names["campaign_orchestrator"],
            handler=self.process_campaign_job,
            prefetch=1,
        )
        
        logger.info("✅ Campaign Orchestrator ready")
    
    async def stop(self) -> None:
        """Stop the orchestrator worker"""
        logger.info("🛑 Campaign Orchestrator stopping...")
        self.running = False
        await self.queue.close()
        await self.db.disconnect()
    
    async def process_campaign_job(self, job: QueueJob) -> None:
        """
        Process a campaign execution job
        
        Args:
            job: Queue job with campaign_id
        """
        campaign_id = UUID(job.payload["campaign_id"])
        
        logger.info(f"📋 Processing campaign {campaign_id}")
        
        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)
                
                async with uow:
                    service = CampaignService(uow, self.queue)
                    
                    # Load campaign
                    campaign = await uow.campaigns.get_by_id(campaign_id)
                    if not campaign:
                        logger.error(f"Campaign {campaign_id} not found")
                        return
                    
                    # Check if should execute
                    if not self._should_execute_campaign(campaign):
                        logger.info(
                            f"Campaign {campaign_id} not ready "
                            f"(status={campaign.status})"
                        )
                        return
                    
                    # Activate campaign if scheduled
                    if campaign.status == CampaignStatus.SCHEDULED:
                        campaign.activate()
                        await uow.campaigns.save(campaign)
                        # Checkpoint commit
                        await uow.commit()
                    
                    # Get recipients (TODO: Load from audience service)
                    recipients = await self._get_campaign_recipients(
                        campaign_id,
                        uow,
                    )
                    
                    if not recipients:
                        logger.warning(f"No recipients for campaign {campaign_id}")
                        return
                    
                    # Load template (TODO: Load from template service)
                    template_content = await self._get_template_content(
                        campaign.template_id,
                        uow,
                    )
                    
                    # Create and queue messages
                    total_created = 0
                    for i in range(0, len(recipients), self.batch_size):
                        batch = recipients[i:i + self.batch_size]
                        
                        # Create messages
                        messages = await self._create_messages(
                            campaign=campaign,
                            recipients=batch,
                            template_content=template_content,
                            uow=uow,
                        )
                        
                        # Queue for delivery
                        await self._queue_messages_for_dispatch(messages)
                        
                        total_created += len(messages)
                        
                        logger.info(
                            f"✅ Created {len(messages)} messages "
                            f"({total_created}/{len(recipients)})"
                        )
                        
                        # Apply rate limiting
                        if campaign.rate_limit:
                            await self._apply_rate_limit(campaign.rate_limit)
                    
                    # Update campaign
                    campaign.recipient_count = total_created
                    await uow.campaigns.save(campaign)
                    # Implicit commit at end of block
                    
                    logger.info(
                        f"🎉 Campaign {campaign_id} orchestrated - "
                        f"{total_created} messages queued"
                    )
                
        except Exception as e:
            logger.exception(f"❌ Error processing campaign {campaign_id}")
            raise
    
    def _should_execute_campaign(self, campaign: Campaign) -> bool:
        """Check if campaign should be executed"""
        if campaign.status not in [
            CampaignStatus.SCHEDULED,
            CampaignStatus.ACTIVE,
        ]:
            return False
        
        # Check if scheduled time has passed
        if campaign.scheduled_for:
            if datetime.utcnow() < campaign.scheduled_for:
                return False
        
        return True
    
    async def _get_campaign_recipients(
        self,
        campaign_id: UUID,
        uow: SQLAlchemyUnitOfWork,
    ) -> List[Dict[str, Any]]:
        """
        Get recipients for campaign
        
        TODO: Integrate with actual audience service
        For now, returns mock data
        
        Args:
            campaign_id: Campaign ID
            uow: Unit of Work
            
        Returns:
            List of recipient dictionaries with phone and variables
        """
        # TODO: Replace with actual audience service call
        # This should query the audience tables and return recipients
        
        # Mock data for now
        return [
            {
                "phone": "+919876543210",
                "name": "John Doe",
                "variables": {"customer_name": "John", "order_id": "1234"},
            },
            {
                "phone": "+919876543211",
                "name": "Jane Smith",
                "variables": {"customer_name": "Jane", "order_id": "1235"},
            },
        ]
    
    async def _get_template_content(
        self,
        template_id: UUID,
        uow: SQLAlchemyUnitOfWork,
    ) -> MessageContent:
        """
        Get template content
        
        TODO: Load from template repository
        For now, returns basic content
        
        Args:
            template_id: Template ID
            uow: Unit of Work
            
        Returns:
            Message content
        """
        # TODO: Load actual template and render
        return MessageContent(
            text="Hi {{customer_name}}, your order {{order_id}} has been confirmed!",
        )
    
    async def _create_messages(
        self,
        campaign: Campaign,
        recipients: List[Dict[str, Any]],
        template_content: MessageContent,
        uow: SQLAlchemyUnitOfWork,
    ) -> List[Message]:
        """
        Create message entities for recipients
        
        Args:
            campaign: Campaign entity
            recipients: List of recipient data
            template_content: Template content
            uow: Unit of Work
            
        Returns:
            Created messages
        """
        messages = []
        
        for recipient in recipients:
            # TODO: Render template with variables
            # For now, use basic substitution
            rendered_text = template_content.text
            for key, value in recipient.get("variables", {}).items():
                rendered_text = rendered_text.replace(
                    f"{{{{{key}}}}}",
                    str(value)
                )
            
            content = MessageContent(text=rendered_text)
            
            # Create message
            message = Message.create(
                campaign_id=campaign.id,
                tenant_id=campaign.tenant_id,
                recipient_phone=recipient["phone"],
                content=content,
                priority=campaign.priority.value,
            )
            
            messages.append(message)
        
        # Bulk save
        messages = await uow.messages.save_batch(messages)
        # Checkpoint commit inside loop
        await uow.commit()
        
        return messages
    
    async def _queue_messages_for_dispatch(
        self,
        messages: List[Message],
    ) -> None:
        """
        Queue messages for dispatcher worker
        
        Args:
            messages: Messages to queue
        """
        queue_messages = []
        
        for message in messages:
            queue_msg = QueueMessage(
                id=str(message.id),
                queue_name=self.settings.queue_names["message_dispatcher"],
                payload={"message_id": str(message.id)},
                priority=self._map_priority(message.priority),
            )
            queue_messages.append(queue_msg)
        
        await self.queue.enqueue_batch(queue_messages)
    
    async def _apply_rate_limit(self, rate_limit: int) -> None:
        """
        Apply rate limiting between batches
        
        Args:
            rate_limit: Messages per second
        """
        # Calculate delay to maintain rate
        delay = self.batch_size / rate_limit
        await asyncio.sleep(delay)
    
    def _map_priority(self, priority: str) -> QueuePriority:
        """Map priority string to queue priority"""
        mapping = {
            "low": QueuePriority.LOW,
            "medium": QueuePriority.MEDIUM,
            "high": QueuePriority.HIGH,
            "urgent": QueuePriority.URGENT,
        }
        return mapping.get(priority, QueuePriority.MEDIUM)


async def main():
    """Main entry point"""
    orchestrator = CampaignOrchestrator()
    
    try:
        await orchestrator.start()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
