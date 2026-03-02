"""
Message Repository Implementation

FIX: _to_model() now serialises template_id + variables into the content JSON blob.
     _to_domain() restores them so the dispatcher always has the template_id it needs.
     save_batch() uses bulk INSERT (not individual saves) for performance.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.message import (
    Message, MessageStatus, MessageChannel, MessageContent, FailureReason
)
from apps.core.ports.repository import MessageRepository, EntityNotFoundException
from apps.adapters.db.models import MessageModel


logger = logging.getLogger(__name__)


class SQLAlchemyMessageRepository(MessageRepository):
    """SQLAlchemy implementation of MessageRepository."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def save(self, message: Message) -> Message:
        """Save message (insert or update)."""
        stmt = select(MessageModel).where(MessageModel.id == message.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            await self._update_from_domain(existing, message)
        else:
            self.session.add(self._to_model(message))

        await self.session.flush()
        return message

    async def save_batch(self, messages: List[Message]) -> List[Message]:
        """
        Bulk-save messages.

        Uses add_all() so SQLAlchemy emits one multi-row INSERT instead of
        N individual round-trips.
        """
        models = [self._to_model(msg) for msg in messages]
        self.session.add_all(models)
        await self.session.flush()
        logger.info("Bulk saved %d messages", len(messages))
        return messages

    async def delete(self, id: UUID) -> bool:
        stmt = select(MessageModel).where(MessageModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_id(self, id: UUID) -> Optional[Message]:
        stmt = select(MessageModel).where(MessageModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def exists(self, id: UUID) -> bool:
        stmt = select(MessageModel.id).where(MessageModel.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_campaign(
        self,
        campaign_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[MessageStatus] = None,
    ) -> List[Message]:
        stmt = select(MessageModel).where(
            MessageModel.campaign_id == campaign_id
        )
        if status:
            stmt = stmt.where(MessageModel.status == status.value)
        stmt = stmt.order_by(MessageModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def get_by_external_id(self, external_id: str) -> Optional[Message]:
        stmt = select(MessageModel).where(MessageModel.external_id == external_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_failed_messages(
        self,
        campaign_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[Message]:
        stmt = select(MessageModel).where(
            MessageModel.status == MessageStatus.FAILED.value
        )
        if campaign_id:
            stmt = stmt.where(MessageModel.campaign_id == campaign_id)
        stmt = stmt.order_by(MessageModel.failed_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def get_pending_fallback(self, limit: int = 100) -> List[Message]:
        stmt = select(MessageModel).where(
            and_(
                MessageModel.status == MessageStatus.FAILED.value,
                MessageModel.fallback_enabled == True,
                MessageModel.fallback_triggered == False,
                MessageModel.channel == MessageChannel.RCS.value,
            )
        ).limit(limit)
        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def update_status(
        self,
        message_id: UUID,
        status: MessageStatus,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        values: Dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.utcnow(),
        }
        if status == MessageStatus.SENT:
            values["sent_at"] = datetime.utcnow()
        elif status == MessageStatus.DELIVERED:
            values["delivered_at"] = datetime.utcnow()
        elif status == MessageStatus.READ:
            values["read_at"] = datetime.utcnow()
        elif status == MessageStatus.FAILED:
            values["failed_at"] = datetime.utcnow()

        if metadata:
            stmt_sel = select(MessageModel.metadata_).where(MessageModel.id == message_id)
            res = await self.session.execute(stmt_sel)
            existing_meta = res.scalar_one_or_none() or {}
            existing_meta.update(metadata)
            values["metadata_"] = existing_meta

        stmt = update(MessageModel).where(MessageModel.id == message_id).values(**values)
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_delivery_stats(self, campaign_id: UUID) -> Dict[str, int]:
        from sqlalchemy import func
        stmt = select(
            func.count(MessageModel.id).label("total"),
            func.count(MessageModel.id).filter(
                MessageModel.status == MessageStatus.SENT.value).label("sent"),
            func.count(MessageModel.id).filter(
                MessageModel.status == MessageStatus.DELIVERED.value).label("delivered"),
            func.count(MessageModel.id).filter(
                MessageModel.status == MessageStatus.FAILED.value).label("failed"),
            func.count(MessageModel.id).filter(
                MessageModel.status == MessageStatus.READ.value).label("read"),
        ).where(MessageModel.campaign_id == campaign_id)
        result = await self.session.execute(stmt)
        row = result.one()
        return {
            "total": row.total or 0,
            "sent": row.sent or 0,
            "delivered": row.delivered or 0,
            "failed": row.failed or 0,
            "read": row.read or 0,
        }

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    def _to_domain(self, model: MessageModel) -> Message:
        """
        Convert ORM model → domain entity.

        CRITICAL FIX: content JSON now stores template_id + variables so they
        survive DB round-trips and are available to the dispatcher.
        """
        raw = model.content or {}

        # Rebuild rich card if stored
        rich_card = None
        if raw.get("rich_card"):
            from apps.core.domain.message import RichCard
            rich_card = RichCard(**raw["rich_card"])

        # Rebuild suggestions if stored
        suggestions = []
        from apps.core.domain.message import SuggestedAction
        for s in raw.get("suggestions", []):
            suggestions.append(SuggestedAction(**s))

        content = MessageContent(
            text=raw.get("text", ""),
            rich_card=rich_card,
            suggestions=suggestions,
            # FIXED: restore template_id and variables from persisted JSON
            template_id=raw.get("template_id"),
            variables=raw.get("variables", []),
        )

        message = Message(
            id=model.id,
            campaign_id=model.campaign_id,
            tenant_id=model.tenant_id,
            recipient_phone=model.recipient_phone,
            content=content,
            status=MessageStatus(model.status),
            channel=MessageChannel(model.channel),
            priority=model.priority,
            created_at=model.created_at,
        )

        # Delivery timestamps
        message.updated_at = model.updated_at
        message.queued_at = model.queued_at
        message.sent_at = model.sent_at
        message.delivered_at = model.delivered_at
        message.read_at = model.read_at
        message.failed_at = model.failed_at
        message.expires_at = model.expires_at

        # Retry / fallback
        message.retry_count = model.retry_count
        message.max_retries = model.max_retries
        message.fallback_enabled = model.fallback_enabled
        message.fallback_triggered = model.fallback_triggered

        # Aggregator
        message.aggregator = model.aggregator
        message.external_id = model.external_id
        if model.failure_reason:
            message.failure_reason = FailureReason(model.failure_reason)

        message.metadata = model.metadata_ or {}
        return message

    def _to_model(self, message: Message) -> MessageModel:
        """
        Convert domain entity → ORM model.

        CRITICAL FIX: template_id and variables are stored inside the content
        JSON blob so they are persisted to the database.
        """
        content_data: Dict[str, Any] = {
            "text": message.content.text,
            # FIXED: persist template_id and variables
            "template_id": message.content.template_id,
            "variables": message.content.variables,
        }

        if message.content.rich_card:
            rc = message.content.rich_card
            content_data["rich_card"] = {
                "title": rc.title,
                "description": rc.description,
                "media_url": rc.media_url,
                "media_type": rc.media_type,
                "media_height": rc.media_height,
            }

        if message.content.suggestions:
            content_data["suggestions"] = [
                {
                    "type": s.type,
                    "text": s.text,
                    "postback_data": s.postback_data,
                    "url": s.url,
                    "phone_number": s.phone_number,
                }
                for s in message.content.suggestions
            ]

        return MessageModel(
            id=message.id,
            campaign_id=message.campaign_id,
            tenant_id=message.tenant_id,
            recipient_phone=message.recipient_phone,
            status=message.status.value,
            channel=message.channel.value,
            priority=message.priority,
            content=content_data,
            queued_at=message.queued_at,
            sent_at=message.sent_at,
            delivered_at=message.delivered_at,
            read_at=message.read_at,
            failed_at=message.failed_at,
            expires_at=message.expires_at,
            retry_count=message.retry_count,
            max_retries=message.max_retries,
            fallback_enabled=message.fallback_enabled,
            fallback_triggered=message.fallback_triggered,
            aggregator=message.aggregator,
            external_id=message.external_id,
            failure_reason=message.failure_reason.value if message.failure_reason else None,
            metadata_=message.metadata,
            created_at=message.created_at,
            updated_at=message.updated_at,
        )

    async def _update_from_domain(self, model: MessageModel, message: Message) -> None:
        """Update mutable fields on existing ORM model."""
        model.status = message.status.value
        model.channel = message.channel.value
        model.queued_at = message.queued_at
        model.sent_at = message.sent_at
        model.delivered_at = message.delivered_at
        model.read_at = message.read_at
        model.failed_at = message.failed_at
        model.retry_count = message.retry_count
        model.fallback_triggered = message.fallback_triggered
        model.aggregator = message.aggregator
        model.external_id = message.external_id
        model.failure_reason = (
            message.failure_reason.value if message.failure_reason else None
        )
        model.metadata_ = message.metadata
        model.updated_at = datetime.utcnow()

        # Update content blob to keep template_id/variables current
        existing_content = model.content or {}
        existing_content["template_id"] = message.content.template_id
        existing_content["variables"] = message.content.variables
        model.content = existing_content
