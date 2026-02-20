"""
Delivery Service

Orchestrates message delivery through RCS/SMS aggregators.
Handles capability checking, message sending, and retry logic.

Responsibilities:
    - Check RCS capability
    - Send messages via appropriate channel
    - Track delivery status
    - Handle failures and retries
    - Queue fallback jobs

Dependencies:
    - MessageRepository
    - AggregatorPort
    - QueuePort
    - OptOutRepository
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
import logging
from datetime import datetime

from apps.core.domain.message import (
    Message,
    MessageContent,
    MessageChannel,
    MessageStatus,
    FailureReason,
)
from apps.core.ports.aggregator import (
    AggregatorPort,
    SendMessageRequest,
    AggregatorException,
    RateLimitException,
)
from apps.core.ports.repository import UnitOfWork
from apps.core.ports.queue import QueuePort, QueueMessage, QueuePriority


logger = logging.getLogger(__name__)


class DeliveryService:
    """
    Message delivery orchestration service
    
    Coordinates message sending across RCS and SMS channels,
    handling capability checks, opt-out validation, and failures.
    
    Example:
        >>> service = DeliveryService(uow, aggregator, queue)
        >>> message = await service.send_message(
        ...     campaign_id=campaign_id,
        ...     recipient_phone="+919876543210",
        ...     content=content,
        ... )
    """
    
    def __init__(
        self,
        uow: UnitOfWork,
        aggregator: AggregatorPort,
        queue: QueuePort,
    ):
        """
        Initialize delivery service
        
        Args:
            uow: Unit of Work for transactions
            aggregator: RCS/SMS aggregator
            queue: Message queue
        """
        self.uow = uow
        self.aggregator = aggregator
        self.queue = queue
    
    async def send_message(
        self,
        campaign_id: UUID,
        tenant_id: UUID,
        recipient_phone: str,
        content: MessageContent,
        priority: str = "medium",
    ) -> Message:
        """
        Send a single message
        
        Args:
            campaign_id: Campaign identifier
            tenant_id: Tenant identifier
            recipient_phone: Recipient phone number
            content: Message content
            priority: Message priority
            
        Returns:
            Created message
            
        Raises:
            ValueError: If recipient has opted out
        """
        async with self.uow:
            # Check opt-out status
            is_opted_out = await self.uow.opt_outs.is_opted_out(
                phone_number=recipient_phone,
                tenant_id=tenant_id,
            )
            
            if is_opted_out:
                raise ValueError(f"Recipient {recipient_phone} has opted out")
            
            # Create message
            message = Message.create(
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                recipient_phone=recipient_phone,
                content=content,
                priority=priority,
            )
            
            # Save message
            message = await self.uow.messages.save(message)
        
        # Queue for async delivery
        await self._queue_message_for_delivery(message)
        
        logger.info(f"Message {message.id} created and queued")
        
        return message
    
    async def send_message_batch(
        self,
        campaign_id: UUID,
        tenant_id: UUID,
        recipients: List[str],
        content: MessageContent,
        priority: str = "medium",
    ) -> List[Message]:
        """
        Send messages to multiple recipients
        
        Args:
            campaign_id: Campaign identifier
            tenant_id: Tenant identifier
            recipients: List of phone numbers
            content: Message content
            priority: Message priority
            
        Returns:
            List of created messages
        """
        messages = []
        
        async with self.uow:
            # Filter out opted-out recipients
            valid_recipients = []
            for phone in recipients:
                is_opted_out = await self.uow.opt_outs.is_opted_out(
                    phone_number=phone,
                    tenant_id=tenant_id,
                )
                if not is_opted_out:
                    valid_recipients.append(phone)
            
            # Create messages
            for phone in valid_recipients:
                message = Message.create(
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    recipient_phone=phone,
                    content=content,
                    priority=priority,
                )
                messages.append(message)
            
            # Batch save
            messages = await self.uow.messages.save_batch(messages)
        
        # Queue all messages
        await self._queue_messages_batch(messages)
        
        logger.info(
            f"Batch: {len(messages)} messages created for campaign {campaign_id}"
        )
        
        return messages
    
    async def process_message_delivery(
        self,
        message_id: UUID,
    ) -> None:
        """
        Process message delivery (called by worker)
        
        Args:
            message_id: Message identifier
        """
        async with self.uow:
            # Load message
            message = await self.uow.messages.get_by_id(message_id)
            if not message:
                logger.error(f"Message {message_id} not found")
                return
            
            try:
                # Check RCS capability
                if message.channel == MessageChannel.RCS:
                    rcs_capable = await self._check_rcs_capability(
                        message.recipient_phone
                    )
                    
                    if not rcs_capable:
                        logger.info(
                            f"Recipient {message.recipient_phone} not RCS capable"
                        )
                        # Trigger immediate fallback
                        message.mark_failed(
                            reason=FailureReason.RCS_NOT_SUPPORTED,
                            error_message="RCS not supported",
                        )
                        await self.uow.messages.save(message)
                        
                        # Queue fallback
                        await self._queue_fallback(message)
                        return
                
                # Queue message
                message.queue()
                await self.uow.messages.save(message)
                
                # Send message
                await self._send_via_aggregator(message)
                
                # Update campaign stats
                await self.uow.campaigns.update_stats(
                    message.campaign_id,
                    {"messages_sent": 1}
                )
                
            except RateLimitException as e:
                logger.warning(
                    f"Rate limit hit for message {message_id}: {e}"
                )
                # Requeue with delay
                await self._requeue_with_delay(message, delay=e.retry_after or 60)
                
            except AggregatorException as e:
                logger.error(
                    f"Aggregator error for message {message_id}: {e}"
                )
                await self._handle_delivery_failure(message, e)
                
            except Exception as e:
                logger.exception(
                    f"Unexpected error processing message {message_id}"
                )
                await self._handle_delivery_failure(message, e)
    
    async def handle_delivery_status_update(
        self,
        external_id: str,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Handle delivery status update from webhook
        
        Args:
            external_id: Vendor's message ID
            status: Delivery status
            error_code: Error code if failed
            error_message: Error message if failed
        """
        async with self.uow:
            # Find message by external ID
            message = await self.uow.messages.get_by_external_id(external_id)
            if not message:
                logger.warning(f"Message with external_id {external_id} not found")
                return
            
            # Update message status
            if status == "delivered":
                message.mark_delivered()
                await self.uow.campaigns.update_stats(
                    message.campaign_id,
                    {"messages_delivered": 1}
                )
            elif status == "read":
                message.mark_read()
                await self.uow.campaigns.update_stats(
                    message.campaign_id,
                    {"messages_read": 1}
                )
            elif status == "failed":
                message.mark_failed(
                    reason=FailureReason.NETWORK_ERROR,
                    error_code=error_code,
                    error_message=error_message,
                )
                # Check for fallback
                if message.should_fallback_to_sms():
                    await self._queue_fallback(message)
            
            await self.uow.messages.save(message)
    
    async def _check_rcs_capability(self, phone_number: str) -> bool:
        """Check if phone number is RCS capable"""
        results = await self.aggregator.check_rcs_capability([phone_number])
        if not results:
            return False
        return results[0].rcs_enabled
    
    async def _send_via_aggregator(self, message: Message) -> None:
        """Send message via aggregator"""
        request = SendMessageRequest(
            message_id=message.id,
            recipient_phone=message.recipient_phone,
            channel=message.channel,
            content_text=message.content.text,
            rich_card=message.content.rich_card,
            suggestions=message.content.suggestions,
            priority=message.priority,
        )
        
        if message.channel == MessageChannel.RCS:
            response = await self.aggregator.send_rcs_message(request)
        else:
            response = await self.aggregator.send_sms_message(request)
            
        if response.success:
            message.mark_sent(
                aggregator=self.aggregator.get_name(),
                external_id=response.external_id,
            )
            await self.uow.messages.save(message)
        else:
            raise AggregatorException(
                message=response.error_message or "Unknown aggregator error",
                error_code=response.error_code,
                retry_after=response.retry_after,
            )
            
    async def _queue_message_for_delivery(self, message: Message) -> None:
        """Queue a single message for delivery"""
        from apps.core.config import get_settings
        settings = get_settings()
        
        await self.queue.enqueue(
            QueueMessage(
                id=str(message.id),
                queue_name=settings.queue_names["message_dispatcher"],
                payload={"message_id": str(message.id)},
                priority=self._map_priority(message.priority),
            )
        )
        
    async def _queue_messages_batch(self, messages: List[Message]) -> None:
        """Queue a batch of messages for delivery"""
        from apps.core.config import get_settings
        settings = get_settings()
        
        queue_messages = [
            QueueMessage(
                id=str(msg.id),
                queue_name=settings.queue_names["message_dispatcher"],
                payload={"message_id": str(msg.id)},
                priority=self._map_priority(msg.priority),
            )
            for msg in messages
        ]
        await self.queue.enqueue_batch(queue_messages)
        
    async def _queue_fallback(self, message: Message) -> None:
        """Queue message for SMS fallback"""
        message.trigger_fallback()
        await self.uow.messages.save(message)
        await self._queue_message_for_delivery(message)
        logger.info(f"Fallback triggered for message {message.id}")
        
    async def _requeue_with_delay(self, message: Message, delay: int) -> None:
        """Requeue message with a delay (for rate limiting)"""
        from apps.core.config import get_settings
        settings = get_settings()
        
        await self.queue.schedule(
            message=QueueMessage(
                id=str(message.id),
                queue_name=settings.queue_names["message_dispatcher"],
                payload={"message_id": str(message.id)},
                priority=self._map_priority(message.priority),
            ),
            scheduled_for=datetime.utcnow(), # Simplified delay logic for now
        )
        
    async def _handle_delivery_failure(self, message: Message, exception: Exception) -> None:
        """Handle delivery failure and decide on retry/fallback"""
        error_code = getattr(exception, 'error_code', 'UNKNOWN')
        message.mark_failed(
            reason=FailureReason.NETWORK_ERROR,
            error_code=error_code,
            error_message=str(exception),
        )
        
        if message.should_retry():
            message.increment_retry()
            await self.uow.messages.save(message)
            await self._queue_message_for_delivery(message)
        elif message.should_fallback_to_sms():
            await self._queue_fallback(message)
        else:
            await self.uow.messages.save(message)
            
    def _map_priority(self, priority: str) -> QueuePriority:
        """Map message priority to queue priority"""
        mapping = {
            "low": QueuePriority.LOW,
            "medium": QueuePriority.MEDIUM,
            "high": QueuePriority.HIGH,
            "urgent": QueuePriority.URGENT,
        }
        return mapping.get(priority.lower(), QueuePriority.MEDIUM)
