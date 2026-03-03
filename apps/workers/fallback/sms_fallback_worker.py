"""
SMS Fallback Worker

Handles automatic fallback when RCS delivery fails.

IMPORTANT — rcssms.in limitation:
    rcssms.in has no separate SMS endpoint. send_sms_message() on
    RcsSmsAdapter sends a BASIC RCS template instead.  True SMS fallback
    requires a dedicated SMS provider (e.g. Exotel, Textlocal, Twilio).
    Wire one in by creating a second adapter and injecting it here.

Flow:
    1. Receive fallback job from queue
    2. Load message from database
    3. Verify fallback should be triggered
    4. Send via aggregator (rcssms BASIC or a true SMS provider)
    5. Update message and campaign stats
"""

import asyncio
import logging
from uuid import UUID

from apps.core.domain.message import MessageChannel, FailureReason
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueJob
from apps.core.ports.aggregator import SendMessageRequest, AggregatorException
from apps.core.aggregators.factory import AggregatorFactory
from apps.adapters.aggregators.mock_adapter import MockAdapter
from apps.core.config import get_settings


import structlog

logger = structlog.get_logger(__name__)



class SMSFallbackWorker:
    """
    SMS Fallback Worker

    Automatically falls back when RCS delivery fails.
    """

    def __init__(
        self,
        db: Database = None,
        queue: RabbitMQAdapter = None,
        aggregator=None,
        concurrency: int = 10,
    ):
        self.db = db or get_database()
        self.queue = queue
        self.aggregator = aggregator
        self.concurrency = concurrency
        self.settings = get_settings()
        self.running = False

    async def start(self) -> None:
        """Start the fallback worker."""
        logger.info("🚀 SMS Fallback Worker starting...")

        await self.db.connect()

        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=self.concurrency,
            )
        await self.queue.connect()

        if not self.aggregator:
            if self.settings.use_mock_aggregator:
                logger.info("🧪 Creating MOCK SMS Fallback Adapter")
                self.aggregator = MockAdapter(
                    success_rate=0.95,
                    delay=0.1,
                    rcs_capable_rate=0.0,
                )
            else:
                self.aggregator = AggregatorFactory.create_sms_adapter(self.settings)
                if not self.aggregator:
                    logger.error(
                        "SMS fallback adapter not configured — "
                        "set SMS_USERNAME / SMS_PASSWORD / SMS_SENDER_ID in .env. "
                        "Worker will consume jobs but skip sending."
                    )

        self.running = True

        await self.queue.subscribe(
            queue_name=self.settings.queue_names["fallback_handler"],
            handler=self.process_fallback_job,
            prefetch=self.concurrency,
        )

        logger.info(
            f"✅ SMS Fallback Worker ready (concurrency={self.concurrency})"
        )

    async def stop(self) -> None:
        """Stop the fallback worker."""
        logger.info("🛑 SMS Fallback Worker stopping...")
        self.running = False

        if self.aggregator:
            await self.aggregator.close()

        await self.queue.close()
        await self.db.disconnect()

    async def process_fallback_job(self, job: QueueJob) -> None:
        """
        Process a fallback job.

        Args:
            job: Queue job with message_id
        """
        message_id = UUID(job.payload["message_id"])

        logger.info(f"📲 Processing fallback for message {message_id}")

        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)

                # Single async-with block — the context manager commits on
                # clean exit.  Do NOT call uow.commit() manually mid-block
                # as that causes a double-commit error.
                async with uow:
                    message = await uow.messages.get_by_id(message_id)
                    if not message:
                        logger.error(f"Message {message_id} not found")
                        return

                    if not message.should_fallback_to_sms():
                        logger.warning(
                            f"Message {message_id} should not fallback "
                            f"(status={message.status}, "
                            f"fallback_triggered={message.fallback_triggered})"
                        )
                        return

                    message.trigger_fallback()
                    await uow.messages.save(message)

                    sms_text = message.content.to_sms_text()

                    logger.info(
                        f"Sending fallback for message {message_id}: "
                        f"{sms_text[:60]}..."
                    )

                    # Build fallback request — use template_id if available,
                    # otherwise send as plain BASIC text.
                    request = SendMessageRequest(
                        message_id=message.id,
                        recipient_phone=message.recipient_phone,
                        channel=MessageChannel.SMS,
                        content_text=sms_text,
                        priority=message.priority,
                        metadata={
                            "template_id": message.content.template_id,
                            "variables": message.content.variables or [],
                        },
                    )

                    try:
                        if not self.aggregator:
                            logger.error(
                                "SMS fallback adapter not configured — "
                                f"dropping fallback for message {message_id}. "
                                "Set SMS_USERNAME / SMS_PASSWORD / SMS_SENDER_ID in .env."
                            )
                            message.mark_failed(
                                reason=FailureReason.NETWORK_ERROR,
                                error_message="SMS adapter not configured",
                            )
                            await uow.messages.save(message)
                            return

                        response = await self.aggregator.send_sms_message(request)

                        if response.success:
                            message.mark_fallback_sent(
                                aggregator=self.aggregator.get_name(),
                                external_id=response.external_id,
                            )
                            await uow.messages.save(message)

                            await uow.campaigns.update_stats(
                                message.campaign_id, {"fallback_triggered": 1}
                            )

                            logger.info(
                                f"✅ Fallback sent for message {message_id} "
                                f"(external_id={response.external_id})"
                            )

                        else:
                            logger.error(
                                f"Fallback failed for message {message_id}: "
                                f"{response.error_message}"
                            )
                            message.mark_failed(
                                reason=FailureReason.NETWORK_ERROR,
                                error_code=response.error_code,
                                error_message=response.error_message,
                            )
                            await uow.messages.save(message)

                            await uow.campaigns.update_stats(
                                message.campaign_id, {"messages_failed": 1}
                            )

                    except AggregatorException as e:
                        logger.error(f"Fallback aggregator error: {e}")
                        message.mark_failed(
                            reason=FailureReason.NETWORK_ERROR,
                            error_message=str(e),
                        )
                        await uow.messages.save(message)

        except Exception:
            logger.exception(f"❌ Error processing fallback for {message_id}")
            raise


async def main():
    """Main entry point."""
    worker = SMSFallbackWorker()

    try:
        await worker.start()

        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
