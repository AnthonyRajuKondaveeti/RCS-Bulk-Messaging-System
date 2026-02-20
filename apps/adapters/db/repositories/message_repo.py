"""
Message Repository Implementation

High-performance message repository with bulk operations.
Optimized for high-volume message handling.

Features:
    - Bulk insert/update
    - Indexed queries (by campaign, status, external_id)
    - Atomic status updates
    - Efficient pagination
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.message import Message, MessageStatus, MessageChannel, MessageContent
from apps.core.ports.repository import MessageRepository, EntityNotFoundException
from apps.adapters.db.models import MessageModel


logger = logging.getLogger(__name__)


class SQLAlchemyMessageRepository(MessageRepository):
    """
    SQLAlchemy implementation of Message Repository
    
    Optimized for high-volume message operations with bulk support.
    
    Example:
        >>> repo = SQLAlchemyMessageRepository(session)
        >>> messages = await repo.save_batch(messages)
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize repository
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
    
    async def save(self, message: Message) -> Message:
        """
        Save message (insert or update)
        
        Args:
            message: Message to save
            
        Returns:
            Saved message
        """
        # Check if exists
        stmt = select(MessageModel).where(MessageModel.id == message.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing
            await self._update_from_domain(existing, message)
        else:
            # Create new
            model = self._to_model(message)
            self.session.add(model)
        
        await self.session.flush()
        
        return message
    
    async def save_batch(
        self,
        messages: List[Message],
    ) -> List[Message]:
        """
        Bulk save messages for performance
        
        Args:
            messages: List of messages to save
            
        Returns:
            Saved messages
        """
        models = [self._to_model(msg) for msg in messages]
        self.session.add_all(models)
        await self.session.flush()
        
        logger.info(f"Bulk saved {len(messages)} messages")
        
        return messages
    
    async def get_by_id(self, id: UUID) -> Optional[Message]:
        """
        Get message by ID
        
        Args:
            id: Message ID
            
        Returns:
            Message or None
        """
        stmt = select(MessageModel).where(MessageModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return self._to_domain(model)
    
    async def delete(self, id: UUID) -> bool:
        """
        Delete message
        
        Args:
            id: Message ID
            
        Returns:
            True if deleted
        """
        stmt = select(MessageModel).where(MessageModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return False
        
        await self.session.delete(model)
        await self.session.flush()
        
        return True
    
    async def exists(self, id: UUID) -> bool:
        """Check if message exists"""
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
        """
        Get messages for a campaign
        
        Args:
            campaign_id: Campaign ID
            limit: Max results
            offset: Pagination offset
            status: Optional status filter
            
        Returns:
            List of messages
        """
        stmt = select(MessageModel).where(
            MessageModel.campaign_id == campaign_id
        )
        
        if status:
            stmt = stmt.where(MessageModel.status == status)
        
        stmt = stmt.order_by(MessageModel.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def get_by_external_id(
        self,
        external_id: str,
    ) -> Optional[Message]:
        """
        Get message by vendor's external ID
        
        Args:
            external_id: Aggregator's message ID
            
        Returns:
            Message or None
        """
        stmt = select(MessageModel).where(
            MessageModel.external_id == external_id
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return self._to_domain(model)
    
    async def get_failed_messages(
        self,
        campaign_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[Message]:
        """
        Get failed messages for retry
        
        Args:
            campaign_id: Optional campaign filter
            limit: Max results
            
        Returns:
            List of failed messages
        """
        stmt = select(MessageModel).where(
            MessageModel.status == MessageStatus.FAILED
        )
        
        if campaign_id:
            stmt = stmt.where(MessageModel.campaign_id == campaign_id)
        
        stmt = stmt.order_by(MessageModel.failed_at.desc())
        stmt = stmt.limit(limit)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def get_pending_fallback(
        self,
        limit: int = 100,
    ) -> List[Message]:
        """
        Get messages needing SMS fallback
        
        Args:
            limit: Max results
            
        Returns:
            Messages that should fallback to SMS
        """
        stmt = select(MessageModel).where(
            and_(
                MessageModel.status == MessageStatus.FAILED,
                MessageModel.fallback_enabled == True,
                MessageModel.fallback_triggered == False,
                MessageModel.channel == MessageChannel.RCS,
            )
        ).limit(limit)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def update_status(
        self,
        message_id: UUID,
        status: MessageStatus,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update message status atomically
        
        Args:
            message_id: Message ID
            status: New status
            metadata: Additional data to update
        """
        values = {
            "status": status,
            "updated_at": datetime.utcnow(),
        }
        
        # Update timestamp fields based on status
        if status == MessageStatus.SENT:
            values["sent_at"] = datetime.utcnow()
        elif status == MessageStatus.DELIVERED:
            values["delivered_at"] = datetime.utcnow()
        elif status == MessageStatus.READ:
            values["read_at"] = datetime.utcnow()
        elif status == MessageStatus.FAILED:
            values["failed_at"] = datetime.utcnow()
        
        if metadata:
            # Merge with existing metadata
            stmt_select = select(MessageModel.metadata_).where(
                MessageModel.id == message_id
            )
            result = await self.session.execute(stmt_select)
            existing_metadata = result.scalar_one_or_none() or {}
            existing_metadata.update(metadata)
            values["metadata_"] = existing_metadata
        
        stmt = (
            update(MessageModel)
            .where(MessageModel.id == message_id)
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()
    
    async def get_delivery_stats(
        self,
        campaign_id: UUID,
    ) -> Dict[str, int]:
        """
        Get aggregated delivery statistics
        
        Args:
            campaign_id: Campaign ID
            
        Returns:
            Stats dictionary with counts
        """
        # Use SQL aggregation for performance
        from sqlalchemy import func
        
        stmt = select(
            func.count(MessageModel.id).label("total"),
            func.count(
                MessageModel.id
            ).filter(
                MessageModel.status == MessageStatus.SENT
            ).label("sent"),
            func.count(
                MessageModel.id
            ).filter(
                MessageModel.status == MessageStatus.DELIVERED
            ).label("delivered"),
            func.count(
                MessageModel.id
            ).filter(
                MessageModel.status == MessageStatus.FAILED
            ).label("failed"),
            func.count(
                MessageModel.id
            ).filter(
                MessageModel.status == MessageStatus.READ
            ).label("read"),
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
    
    def _to_domain(self, model: MessageModel) -> Message:
        """Convert ORM model to domain entity"""
        # Parse content from JSON
        content_data = model.content
        content = MessageContent(
            text=content_data.get("text", ""),
            rich_card=content_data.get("rich_card"),
            suggestions=content_data.get("suggestions", []),
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
        
        # Set timestamps
        message.updated_at = model.updated_at
        message.queued_at = model.queued_at
        message.sent_at = model.sent_at
        message.delivered_at = model.delivered_at
        message.read_at = model.read_at
        message.failed_at = model.failed_at
        message.expires_at = model.expires_at
        
        # Set retry/fallback
        message.retry_count = model.retry_count
        message.max_retries = model.max_retries
        message.fallback_enabled = model.fallback_enabled
        message.fallback_triggered = model.fallback_triggered
        
        # Set aggregator details
        message.aggregator = model.aggregator
        message.external_id = model.external_id
        if model.failure_reason:
            from apps.core.domain.message import FailureReason
            message.failure_reason = FailureReason(model.failure_reason)
        
        message.metadata = model.metadata_ or {}
        
        return message
    
    def _to_model(self, message: Message) -> MessageModel:
        """Convert domain entity to ORM model"""
        # Serialize content to JSON
        content_data = {
            "text": message.content.text,
        }
        if message.content.rich_card:
            content_data["rich_card"] = {
                "title": message.content.rich_card.title,
                "description": message.content.rich_card.description,
                "media_url": message.content.rich_card.media_url,
                "media_type": message.content.rich_card.media_type,
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
    
    async def _update_from_domain(
        self,
        model: MessageModel,
        message: Message,
    ) -> None:
        """Update ORM model from domain entity"""
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
