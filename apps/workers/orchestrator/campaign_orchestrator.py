"""
Campaign Orchestrator Worker

Executes campaigns by expanding audiences into individual messages
and queuing them for delivery.

Flow:
    1. Receive campaign job from queue
    2. Load campaign and template from DB
    3. Load recipients from audience
    4. Create Message entities with template_id + variables
    5. Bulk save to database
    6. Queue messages for dispatcher
    7. Update campaign statistics
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, List, Dict, Any
from uuid import UUID
from datetime import datetime

from apps.core.domain.campaign import Campaign, CampaignStatus
from apps.core.domain.message import Message, MessageContent
from apps.core.services.campaign_service import CampaignService
from apps.adapters.db.postgres import Database, get_database
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.ports.queue import QueueMessage, QueuePriority, QueueJob
from apps.core.config import get_settings


from apps.core.observability.logging import configure_structlog

configure_structlog(service="worker")

logger = logging.getLogger(__name__)


def _log(level: str, msg: str, **ctx):
    """Emit a structured log with consistent context fields."""
    getattr(logger, level)(msg, extra=ctx)


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
        self.db = db or get_database()
        self.queue = queue
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.settings = get_settings()
        self.running = False

    async def start(self) -> None:
        """Start the orchestrator worker"""
        logger.info("🚀 Campaign Orchestrator starting...")

        await self.db.connect()

        if not self.queue:
            self.queue = RabbitMQAdapter(
                url=self.settings.rabbitmq.url,
                prefetch_count=1,
            )
        await self.queue.connect()

        self.running = True

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
        Process a campaign execution job.

        Args:
            job: Queue job with campaign_id
        """
        campaign_id = UUID(job.payload["campaign_id"])
        start_time = time.monotonic()

        _log("info", "STEP 1/7 | Campaign job received — starting orchestration",
             campaign_id=str(campaign_id), step="job_received")

        try:
            async with self.db.session() as session:
                uow = SQLAlchemyUnitOfWork(session)

                async with uow:
                    service = CampaignService(uow, self.queue)

                    # STEP 2: Load campaign
                    campaign = await uow.campaigns.get_by_id(campaign_id)
                    if not campaign:
                        _log("error", "STEP 2/7 | Campaign not found — aborting",
                             campaign_id=str(campaign_id), step="load_campaign")
                        return

                    _log("info", "STEP 2/7 | Campaign loaded",
                         campaign_id=str(campaign_id),
                         campaign_name=campaign.name,
                         campaign_status=str(campaign.status),
                         template_id=str(campaign.template_id),
                         step="load_campaign")

                    if not self._should_execute_campaign(campaign):
                        _log("info", "STEP 2/7 | Campaign not ready for execution — skipping",
                             campaign_id=str(campaign_id),
                             campaign_status=str(campaign.status),
                             step="readiness_check")
                        return

                    # Activate if scheduled
                    if campaign.status == CampaignStatus.SCHEDULED:
                        campaign.activate()
                        await uow.campaigns.save(campaign)
                        await uow.commit()
                        _log("info", "STEP 2/7 | Scheduled campaign activated",
                             campaign_id=str(campaign_id), step="activate")

                    # STEP 3: Load and validate template
                    template = await uow.templates.get_by_id(campaign.template_id)
                    if not template:
                        _log("error", "STEP 3/7 | Template not found — cannot send",
                             campaign_id=str(campaign_id),
                             template_id=str(campaign.template_id),
                             step="load_template")
                        return

                    if template.status != "approved":
                        _log("error", "STEP 3/7 | Template not approved — cannot send",
                             campaign_id=str(campaign_id),
                             template_id=str(campaign.template_id),
                             template_status=template.status,
                             step="validate_template",
                             hint="Submit template to rcssms.in and wait for APPROVED status")
                        return

                    if not getattr(template, 'external_template_id', None):
                        _log("error", "STEP 3/7 | Template has no external_template_id — cannot send",
                             campaign_id=str(campaign_id),
                             template_id=str(campaign.template_id),
                             step="validate_template",
                             hint="Store the rcssms.in templateid in external_template_id field")
                        return

                    _log("info", "STEP 3/7 | Template loaded and validated",
                         campaign_id=str(campaign_id),
                         template_id=str(campaign.template_id),
                         template_name=template.name,
                         external_template_id=template.external_template_id,
                         rcs_type=getattr(template, 'rcs_type', 'BASIC'),
                         variable_count=len(template.variables),
                         step="load_template")

                    # STEP 4 / 5 / 6: Stream recipients in batches and create+queue messages
                    # Memory usage is bounded to batch_size rows at a time.
                    total_created = 0
                    batch_num = 0
                    found_any = False

                    async for recipient_batch in self._stream_campaign_recipients(
                        campaign_id=campaign_id, uow=uow
                    ):
                        found_any = True
                        batch_num += 1

                        _log(
                            "info",
                            "STEP 4-5/7 | Creating message batch from streamed contacts",
                            campaign_id=str(campaign_id),
                            batch_number=batch_num,
                            batch_size=len(recipient_batch),
                            step="stream_batch",
                        )

                        messages = await self._create_messages(
                            campaign=campaign,
                            recipients=recipient_batch,
                            template=template,
                            uow=uow,
                        )

                        _log(
                            "info",
                            "STEP 6/7 | Queuing batch for dispatch",
                            campaign_id=str(campaign_id),
                            batch_number=batch_num,
                            messages_queued=len(messages),
                            step="queue_messages",
                        )

                        await self._queue_messages_for_dispatch(messages)
                        total_created += len(messages)

                        if campaign.rate_limit:
                            await self._apply_rate_limit(campaign.rate_limit)

                    if not found_any:
                        _log(
                            "warning",
                            "STEP 4/7 | No recipients found — campaign has nothing to send",
                            campaign_id=str(campaign_id),
                            step="load_recipients",
                            hint="Ensure audience_ids are set in campaign metadata "
                                 "and audiences have contacts in audience_contacts table",
                        )
                        return

                    # STEP 7: Finalize
                    campaign.recipient_count = total_created
                    await uow.campaigns.save(campaign)

                    elapsed = round(time.monotonic() - start_time, 2)
                    _log("info", "STEP 7/7 | Campaign orchestration complete",
                         campaign_id=str(campaign_id),
                         campaign_name=campaign.name,
                         total_messages_queued=total_created,
                         elapsed_seconds=elapsed,
                         step="complete")

        except Exception as e:
            elapsed = round(time.monotonic() - start_time, 2)
            _log("exception", "Campaign orchestration failed",
                 campaign_id=str(campaign_id),
                 error=str(e),
                 elapsed_seconds=elapsed,
                 step="failed")
            raise

    def _should_execute_campaign(self, campaign: Campaign) -> bool:
        """Check if campaign should be executed"""
        if campaign.status not in [
            CampaignStatus.SCHEDULED,
            CampaignStatus.ACTIVE,
        ]:
            return False

        if campaign.scheduled_for:
            if datetime.utcnow() < campaign.scheduled_for:
                return False

        return True

    async def _stream_campaign_recipients(
        self,
        campaign_id: UUID,
        uow: SQLAlchemyUnitOfWork,
    ) -> AsyncGenerator[List[Dict[str, Any]], None]:
        """
        Stream recipients for a campaign in batches from the audience_contacts table.

        This is the memory-safe replacement for the old `_get_campaign_recipients`
        which loaded all contacts into a Python list in one shot.

        How it works:
          1. Read audience_ids from campaign.metadata_
          2. For each audience, call repo.stream_contacts() — an async generator
             that issues keyset-paginated DB queries (1 000 rows at a time)
          3. Yield each batch as a list of {phone, variables} dicts

        Memory usage: bounded to batch_size rows (1 000) regardless of audience size.

        Example:
            async for batch in self._stream_campaign_recipients(campaign_id, uow):
                # batch is List[Dict[str, Any]] with keys: phone, variables
                messages = await self._create_messages(campaign, batch, template, uow)

        Args:
            campaign_id: Campaign being executed
            uow:         Unit of Work (provides the audience repository)

        Yields:
            List[Dict[str, Any]] — each dict has {"phone": str, "variables": list}
        """
        from apps.adapters.db.models import CampaignModel
        from sqlalchemy import select

        # Resolve audience IDs from the campaign row
        stmt = select(CampaignModel).where(CampaignModel.id == campaign_id)
        result = await uow.session.execute(stmt)
        campaign_model = result.scalar_one_or_none()

        if not campaign_model:
            return

        audience_ids = campaign_model.metadata_.get("audience_ids", [])
        if not audience_ids:
            logger.warning(
                "Campaign %s has no audience_ids in metadata. "
                "Add audience_ids list to campaign.metadata before activating.",
                campaign_id,
            )
            return

        for audience_id in audience_ids:
            logger.info(
                "Streaming contacts for audience %s (campaign %s)",
                audience_id, campaign_id,
            )

            # stream_contacts() is an async generator — each iteration yields
            # at most 1 000 AudienceContactModel rows without loading the rest.
            async for contact_rows in uow.audiences.stream_contacts(audience_id):
                batch: List[Dict[str, Any]] = []
                for row in contact_rows:
                    phone = row.phone_number
                    if not phone:
                        continue
                    batch.append({
                        "phone": phone,
                        "variables": row.variables or [],
                    })

                if batch:
                    yield batch

    async def _get_template_content(
        self,
        template,
        recipient_variables: List[Any],
    ) -> MessageContent:
        """
        Build MessageContent from template and per-recipient variable values.

        Args:
            template:            Template domain object (must have external_template_id)
            recipient_variables: Ordered list of variable values for this recipient

        Returns:
            MessageContent with template_id and variables set
        """
        # Render text locally for fallback/preview purposes
        rendered_text = template.content
        for i, var in enumerate(template.variables):
            if i < len(recipient_variables):
                placeholder = f"{{{{{var.name}}}}}"
                rendered_text = rendered_text.replace(
                    placeholder, str(recipient_variables[i])
                )

        return MessageContent(
            text=rendered_text,
            template_id=template.external_template_id,  # rcssms.in approved template ID
            variables=recipient_variables,               # ordered values for rcssms API
            rcs_type=getattr(template, 'rcs_type', 'BASIC'),  # BASIC|RICH|RICHCASOUREL
        )

    async def _create_messages(
        self,
        campaign: Campaign,
        recipients: List[Dict[str, Any]],
        template,
        uow: SQLAlchemyUnitOfWork,
    ) -> List[Message]:
        """
        Create message entities for a batch of recipients.

        Args:
            campaign:   Campaign entity
            recipients: List of recipient data with phone and variables
            template:   Template domain object
            uow:        Unit of Work

        Returns:
            Created and saved messages
        """
        messages = []

        for recipient in recipients:
            content = await self._get_template_content(
                template=template,
                recipient_variables=recipient.get("variables", []),
            )

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
        await uow.commit()

        return messages

    async def _queue_messages_for_dispatch(
        self,
        messages: List[Message],
    ) -> None:
        """Queue messages for dispatcher worker"""
        queue_messages = [
            QueueMessage(
                id=str(message.id),
                queue_name=self.settings.queue_names["message_dispatcher"],
                payload={"message_id": str(message.id)},
                priority=self._map_priority(message.priority),
            )
            for message in messages
        ]

        await self.queue.enqueue_batch(queue_messages)

    async def _apply_rate_limit(self, rate_limit: int) -> None:
        """Apply rate limiting between batches"""
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

        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
