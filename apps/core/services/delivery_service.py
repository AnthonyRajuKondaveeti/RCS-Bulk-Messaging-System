"""
Delivery Service

Orchestrates message delivery through RCS/SMS aggregators.
Handles capability checking, message sending, and retry logic.

Dependencies:
    - MessageRepository
    - AggregatorPort
    - QueuePort
    - OptOutRepository
"""

from typing import Optional, List
from uuid import UUID
import logging
import time
from datetime import datetime, timedelta, timezone

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
    Message delivery orchestration service.

    Coordinates message sending across RCS and SMS channels,
    handling capability checks, opt-out validation, and failures.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        aggregator: AggregatorPort,
        queue: QueuePort,
    ):
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
        Send a single message.

        Args:
            campaign_id:     Campaign identifier
            tenant_id:       Tenant identifier
            recipient_phone: Recipient phone number
            content:         Message content (must include template_id)
            priority:        Message priority

        Returns:
            Created message

        Raises:
            ValueError: If recipient has opted out
        """
        async with self.uow:
            is_opted_out = await self.uow.opt_outs.is_opted_out(
                phone_number=recipient_phone,
                tenant_id=tenant_id,
            )

            if is_opted_out:
                raise ValueError(f"Recipient {recipient_phone} has opted out")

            message = Message.create(
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                recipient_phone=recipient_phone,
                content=content,
                priority=priority,
            )

            message = await self.uow.messages.save(message)

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
        Send messages to multiple recipients.
        """
        messages = []

        async with self.uow:
            valid_recipients = []
            for phone in recipients:
                is_opted_out = await self.uow.opt_outs.is_opted_out(
                    phone_number=phone,
                    tenant_id=tenant_id,
                )
                if not is_opted_out:
                    valid_recipients.append(phone)

            for phone in valid_recipients:
                message = Message.create(
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    recipient_phone=phone,
                    content=content,
                    priority=priority,
                )
                messages.append(message)

            messages = await self.uow.messages.save_batch(messages)

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
        Process message delivery (called by dispatcher worker).
        """
        start_time = time.monotonic()

        async with self.uow:
            message = await self.uow.messages.get_by_id(message_id)
            if not message:
                logger.error(
                    "Message not found — skipping",
                    extra={"message_id": str(message_id), "step": "load_message"},
                )
                return

            logger.info(
                "Message loaded — starting delivery",
                extra={
                    "message_id": str(message_id),
                    "campaign_id": str(message.campaign_id),
                    "recipient": message.recipient_phone,
                    "channel": str(message.channel),
                    "template_id": message.content.template_id,
                    "variable_count": len(message.content.variables or []),
                    "retry_count": message.retry_count,
                    "step": "load_message",
                },
            )

            # Only RCS messages require template_id (SMS fallback messages don't)
            if message.channel == MessageChannel.RCS and not message.content.template_id:
                logger.error(
                    "template_id missing — cannot send RCS via rcssms.in",
                    extra={
                        "message_id": str(message_id),
                        "campaign_id": str(message.campaign_id),
                        "step": "validate",
                        "hint": "Check orchestrator _get_template_content sets template_id",
                    },
                )
                message.mark_failed(
                    reason=FailureReason.UNKNOWN,
                    error_message="template_id missing in MessageContent",
                )
                await self.uow.messages.save(message)
                return

            try:
                if message.channel == MessageChannel.RCS:
                    logger.info(
                        "Checking RCS capability",
                        extra={
                            "message_id": str(message_id),
                            "recipient": message.recipient_phone,
                            "step": "rcs_capability_check",
                        },
                    )
                    rcs_capable = await self._check_rcs_capability(
                        message.recipient_phone
                    )

                    if not rcs_capable:
                        logger.info(
                            "Recipient not RCS capable — marking failed and creating SMS fallback",
                            extra={
                                "message_id": str(message_id),
                                "recipient": message.recipient_phone,
                                "step": "rcs_capability_check",
                            },
                        )
                        message.mark_failed(
                            reason=FailureReason.RCS_NOT_SUPPORTED,
                            error_message="RCS not supported",
                        )
                        await self.uow.messages.save(message)
                        await self._handle_fallback_inline(message)
                        # Recalculate stats after fallback
                        await self.uow.campaigns.recalculate_stats(message.campaign_id)
                        return

                    logger.info(
                        "Recipient is RCS capable — proceeding",
                        extra={
                            "message_id": str(message_id),
                            "recipient": message.recipient_phone,
                            "step": "rcs_capability_check",
                        },
                    )

                message.queue()
                await self.uow.messages.save(message)

                logger.info(
                    "Sending message via rcssms.in",
                    extra={
                        "message_id": str(message_id),
                        "recipient": message.recipient_phone,
                        "template_id": message.content.template_id,
                        "variables": message.content.variables,
                        "step": "send",
                    },
                )

                await self._send_via_aggregator(message)

                elapsed = round(time.monotonic() - start_time, 3)
                logger.info(
                    "Message sent successfully",
                    extra={
                        "message_id": str(message_id),
                        "campaign_id": str(message.campaign_id),
                        "recipient": message.recipient_phone,
                        "external_id": message.external_id,
                        "aggregator": message.aggregator,
                        "elapsed_seconds": elapsed,
                        "step": "sent",
                    },
                )
                
                # Don't recalculate stats on every successful send (causes deadlocks)
                # Stats are recalculated on failures and fallback creation only

            except RateLimitException as e:
                logger.warning(
                    "Rate limit hit — requeueing with delay",
                    extra={
                        "message_id": str(message_id),
                        "retry_after_seconds": e.retry_after,
                        "step": "rate_limited",
                    },
                )
                await self._requeue_with_delay(message, delay=e.retry_after or 60)

            except AggregatorException as e:
                logger.error(
                    "Aggregator returned error",
                    extra={
                        "message_id": str(message_id),
                        "recipient": message.recipient_phone,
                        "error_code": e.error_code,
                        "error": str(e),
                        "retry_count": message.retry_count,
                        "step": "aggregator_error",
                    },
                )
                await self._handle_delivery_failure(message, e)

            except Exception as e:
                logger.exception(
                    "Unexpected error during delivery",
                    extra={
                        "message_id": str(message_id),
                        "recipient": message.recipient_phone,
                        "error": str(e),
                        "step": "unexpected_error",
                    },
                )
                await self._handle_delivery_failure(message, e)

    async def handle_delivery_status_update(
        self,
        external_id: str,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Handle delivery status update from DLR webhook."""
        async with self.uow:
            message = await self.uow.messages.get_by_external_id(external_id)
            if not message:
                logger.warning(
                    "DLR received but message not found",
                    extra={
                        "external_id": external_id,
                        "status": status,
                        "step": "dlr_lookup",
                    },
                )
                return

            logger.info(
                "DLR status update received",
                extra={
                    "message_id": str(message.id),
                    "external_id": external_id,
                    "dlr_status": status,
                    "recipient": message.recipient_phone,
                    "step": "dlr_update",
                },
            )

            if status == "delivered":
                message.mark_delivered()
            elif status == "read":
                message.mark_read()
            elif status == "failed":
                message.mark_failed(
                    reason=FailureReason.NETWORK_ERROR,
                    error_code=error_code,
                    error_message=error_message,
                )
                logger.warning(
                    "Message delivery failed per DLR",
                    extra={
                        "message_id": str(message.id),
                        "external_id": external_id,
                        "error_code": error_code,
                        "step": "dlr_failed",
                    },
                )
                if message.should_trigger_fallback():
                    await self._handle_fallback_inline(message)

            await self.uow.messages.save(message)
            
            # Recalculate campaign stats after terminal state change
            await self.uow.campaigns.recalculate_stats(message.campaign_id)

    async def _check_rcs_capability(self, phone_number: str) -> bool:
        """Check if phone number is RCS capable."""
        results = await self.aggregator.check_rcs_capability([phone_number])
        if not results:
            return False
        return results[0].rcs_enabled

    async def _send_via_aggregator(self, message: Message) -> None:
        """
        Send message via aggregator.

        Passes template_id and variables from MessageContent into metadata
        so the rcssms adapter can build the correct payload.
        """
        request = SendMessageRequest(
            message_id=message.id,
            recipient_phone=message.recipient_phone,
            channel=message.channel,
            content_text=message.content.text,
            rich_card=message.content.rich_card,
            suggestions=message.content.suggestions,
            priority=message.priority,
            metadata={
                "template_id": message.content.template_id,
                "variables": message.content.variables or [],
                "rcs_type": message.content.rcs_type,   # BASIC|RICH|RICHCASOUREL
            },
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
        """Queue a single message for delivery."""
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
        """Queue a batch of messages for delivery."""
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

    async def _handle_fallback_inline(self, message: Message) -> None:
        """
        Handle SMS fallback inline (NEW ARCHITECTURE).
        
        Creates a NEW SMS message linked to the parent instead of modifying
        the existing message. This fixes the FAILED → PENDING state violation.
        
        Parent message stays FAILED (terminal), child message starts fresh.
        """
        if not message.should_trigger_fallback():
            logger.warning(
                "Fallback not triggered - conditions not met",
                extra={
                    "message_id": str(message.id),
                    "status": message.status.value,
                    "channel": message.channel.value,
                    "step": "fallback_check",
                },
            )
            return
        
        # Create NEW SMS message (doesn't modify parent)
        fallback_message = message.create_fallback_message()
        
        # Save child message
        await self.uow.messages.save(fallback_message)
        
        # Queue child for delivery (standard dispatcher queue)
        await self._queue_message_for_delivery(fallback_message)
        
        logger.info(
            "SMS fallback message created and queued",
            extra={
                "parent_message_id": str(message.id),
                "fallback_message_id": str(fallback_message.id),
                "recipient": message.recipient_phone,
                "failure_reason": str(message.failure_reason),
                "channel": "SMS",
                "step": "fallback_created",
            },
        )

    async def _requeue_with_delay(self, message: Message, delay: int) -> None:
        """Requeue message with a delay (for rate limiting).

        FIX: previously passed datetime.utcnow() as scheduled_for, ignoring
        the delay parameter entirely.  Now correctly schedules in the future.
        """
        from apps.core.config import get_settings

        settings = get_settings()

        scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=delay)

        await self.queue.schedule(
            message=QueueMessage(
                id=str(message.id),
                queue_name=settings.queue_names["message_dispatcher"],
                payload={"message_id": str(message.id)},
                priority=self._map_priority(message.priority),
            ),
            scheduled_for=scheduled_for,
        )

    async def _handle_delivery_failure(
        self,
        message: Message,
        exception: Exception,
    ) -> None:
        """
        Handle delivery failure and decide on retry/fallback.
        
        NEW: Creates child fallback message instead of modifying parent.
        """
        error_code = getattr(exception, "error_code", "UNKNOWN")
        
        # Determine failure reason from exception type
        failure_reason = FailureReason.NETWORK_ERROR
        if hasattr(exception, "error_code"):
            # Map specific error codes to failure reasons
            if "rcs" in error_code.lower():
                failure_reason = FailureReason.RCS_NOT_SUPPORTED
            elif "aggregator" in error_code.lower():
                failure_reason = FailureReason.AGGREGATOR_ERROR
        
        message.mark_failed(
            reason=failure_reason,
            error_code=error_code,
            error_message=str(exception),
        )

        if message.should_retry():
            message.increment_retry()
            await self.uow.messages.save(message)
            await self._queue_message_for_delivery(message)
            logger.info(
                "Message scheduled for retry",
                extra={
                    "message_id": str(message.id),
                    "retry_count": message.retry_count,
                    "max_retries": message.max_retries,
                    "error_code": error_code,
                    "step": "retry_queued",
                },
            )
        elif message.should_trigger_fallback():
            # Save parent as FAILED first
            await self.uow.messages.save(message)
            # Create and queue child fallback message
            await self._handle_fallback_inline(message)
            # Recalculate stats after fallback
            await self.uow.campaigns.recalculate_stats(message.campaign_id)
        else:
            await self.uow.messages.save(message)
            # Recalculate stats for permanent failure
            await self.uow.campaigns.recalculate_stats(message.campaign_id)
            logger.error(
                "Message permanently failed — no retry or fallback possible",
                extra={
                    "message_id": str(message.id),
                    "recipient": message.recipient_phone,
                    "retry_count": message.retry_count,
                    "error_code": error_code,
                    "failure_reason": message.failure_reason.value if message.failure_reason else None,
                    "step": "permanently_failed",
                },
            )

    def _map_priority(self, priority: str) -> QueuePriority:
        """Map message priority to queue priority."""
        mapping = {
            "low": QueuePriority.LOW,
            "medium": QueuePriority.MEDIUM,
            "high": QueuePriority.HIGH,
            "urgent": QueuePriority.URGENT,
        }
        return mapping.get(priority.lower(), QueuePriority.MEDIUM)
