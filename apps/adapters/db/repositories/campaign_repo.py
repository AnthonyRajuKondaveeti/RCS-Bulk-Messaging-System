"""
Campaign Repository Implementation

Concrete implementation of CampaignRepository using SQLAlchemy.
Handles campaign persistence with optimized queries.

Features:
    - CRUD operations
    - Tenant-based queries
    - Status filtering
    - Scheduled campaign queries
    - Statistics updates (atomic)
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.campaign import Campaign, CampaignStatus, CampaignType, Priority
from apps.core.ports.repository import CampaignRepository, EntityNotFoundException
from apps.adapters.db.models import CampaignModel


logger = logging.getLogger(__name__)


class SQLAlchemyCampaignRepository(CampaignRepository):
    """
    SQLAlchemy implementation of Campaign Repository
    
    Handles mapping between Campaign domain model and CampaignModel ORM.
    
    Example:
        >>> repo = SQLAlchemyCampaignRepository(session)
        >>> campaign = await repo.save(campaign)
        >>> campaigns = await repo.get_by_tenant(tenant_id)
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize repository
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
    
    async def save(self, campaign: Campaign) -> Campaign:
        """
        Save campaign (insert or update)
        
        Args:
            campaign: Campaign to save
            
        Returns:
            Saved campaign with updated timestamps
        """
        # Check if exists
        stmt = select(CampaignModel).where(CampaignModel.id == campaign.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing
            await self._update_from_domain(existing, campaign)
        else:
            # Create new
            model = self._to_model(campaign)
            self.session.add(model)
        
        await self.session.flush()
        
        logger.debug(f"Saved campaign {campaign.id}")
        
        return campaign
    
    async def get_by_id(self, id: UUID) -> Optional[Campaign]:
        """
        Get campaign by ID
        
        Args:
            id: Campaign ID
            
        Returns:
            Campaign or None if not found
        """
        stmt = select(CampaignModel).where(CampaignModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return self._to_domain(model)
    
    async def delete(self, id: UUID) -> bool:
        """
        Delete campaign
        
        Args:
            id: Campaign ID
            
        Returns:
            True if deleted, False if not found
        """
        stmt = select(CampaignModel).where(CampaignModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return False
        
        await self.session.delete(model)
        await self.session.flush()
        
        logger.info(f"Deleted campaign {id}")
        
        return True
    
    async def exists(self, id: UUID) -> bool:
        """
        Check if campaign exists
        
        Args:
            id: Campaign ID
            
        Returns:
            True if exists
        """
        stmt = select(CampaignModel.id).where(CampaignModel.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
    
    async def get_by_tenant(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[CampaignStatus] = None,
    ) -> List[Campaign]:
        """
        Get campaigns for a tenant
        
        Args:
            tenant_id: Tenant ID
            limit: Max results
            offset: Pagination offset
            status: Optional status filter
            
        Returns:
            List of campaigns
        """
        stmt = select(CampaignModel).where(
            CampaignModel.tenant_id == tenant_id
        )
        
        if status:
            stmt = stmt.where(CampaignModel.status == status)
        
        stmt = stmt.order_by(CampaignModel.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def get_scheduled_campaigns(
        self,
        before: datetime,
    ) -> List[Campaign]:
        """
        Get campaigns scheduled before a time
        
        Args:
            before: Scheduled time threshold
            
        Returns:
            Campaigns ready for execution
        """
        stmt = select(CampaignModel).where(
            and_(
                CampaignModel.status == CampaignStatus.SCHEDULED,
                CampaignModel.scheduled_for <= before,
            )
        ).order_by(CampaignModel.scheduled_for)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def get_active_campaigns(
        self,
        tenant_id: Optional[UUID] = None,
    ) -> List[Campaign]:
        """
        Get all active campaigns
        
        Args:
            tenant_id: Optional tenant filter
            
        Returns:
            List of active campaigns
        """
        stmt = select(CampaignModel).where(
            CampaignModel.status == CampaignStatus.ACTIVE
        )
        
        if tenant_id:
            stmt = stmt.where(CampaignModel.tenant_id == tenant_id)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def update_stats(
        self,
        campaign_id: UUID,
        stats_update: Dict[str, int],
    ) -> None:
        """
        Update campaign statistics atomically
        
        Args:
            campaign_id: Campaign ID
            stats_update: Stats to increment (e.g., {"messages_sent": 1})
        """
        # Build update statement with increments
        values = {}
        for key, increment in stats_update.items():
            if hasattr(CampaignModel, key):
                values[key] = getattr(CampaignModel, key) + increment
        
        if values:
            stmt = (
                update(CampaignModel)
                .where(CampaignModel.id == campaign_id)
                .values(**values)
            )
            await self.session.execute(stmt)
            await self.session.flush()
            
            logger.debug(f"Updated stats for campaign {campaign_id}: {stats_update}")
    
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Campaign]:
        """
        Search campaigns with filters
        
        Args:
            tenant_id: Tenant context
            query: Search query string
            filters: Additional filters
            
        Returns:
            Matching campaigns
        """
        stmt = select(CampaignModel).where(
            CampaignModel.tenant_id == tenant_id
        )
        
        # Simple name search
        if query:
            stmt = stmt.where(
                CampaignModel.name.ilike(f"%{query}%")
            )
        
        # Apply additional filters
        if filters:
            if "status" in filters:
                stmt = stmt.where(CampaignModel.status == filters["status"])
            if "campaign_type" in filters:
                stmt = stmt.where(
                    CampaignModel.campaign_type == filters["campaign_type"]
                )
            if "tags" in filters:
                # Filter by tags (JSON contains)
                for tag in filters["tags"]:
                    stmt = stmt.where(
                        CampaignModel.tags.contains([tag])
                    )
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    def _to_domain(self, model: CampaignModel) -> Campaign:
        """
        Convert ORM model to domain entity
        
        Args:
            model: SQLAlchemy model
            
        Returns:
            Campaign domain entity
        """
        campaign = Campaign(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            campaign_type=CampaignType(model.campaign_type),
            template_id=model.template_id,
            status=CampaignStatus(model.status),
            priority=Priority(model.priority),
            scheduled_for=model.scheduled_for,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        
        # Set configuration
        campaign.enable_fallback = model.enable_fallback
        campaign.fallback_channel = model.fallback_channel
        campaign.rate_limit = model.rate_limit
        
        # Set statistics
        campaign.stats.total_recipients = model.recipient_count
        campaign.stats.messages_sent = model.messages_sent
        campaign.stats.messages_delivered = model.messages_delivered
        campaign.stats.messages_failed = model.messages_failed
        campaign.stats.messages_read = model.messages_read
        campaign.stats.fallback_triggered = model.fallback_triggered
        campaign.stats.opt_outs = model.opt_outs
        
        # Set metadata
        campaign.metadata = model.metadata_ or {}
        campaign.tags = model.tags or []
        campaign.recipient_count = model.recipient_count
        
        return campaign
    
    def _to_model(self, campaign: Campaign) -> CampaignModel:
        """
        Convert domain entity to ORM model
        
        Args:
            campaign: Campaign domain entity
            
        Returns:
            SQLAlchemy model
        """
        return CampaignModel(
            id=campaign.id,
            tenant_id=campaign.tenant_id,
            name=campaign.name,
            status=campaign.status.value,
            campaign_type=campaign.campaign_type.value,
            priority=campaign.priority.value,
            template_id=campaign.template_id,
            scheduled_for=campaign.scheduled_for,
            enable_fallback=campaign.enable_fallback,
            fallback_channel=campaign.fallback_channel,
            rate_limit=campaign.rate_limit,
            recipient_count=campaign.recipient_count,
            messages_sent=campaign.stats.messages_sent,
            messages_delivered=campaign.stats.messages_delivered,
            messages_failed=campaign.stats.messages_failed,
            messages_read=campaign.stats.messages_read,
            fallback_triggered=campaign.stats.fallback_triggered,
            opt_outs=campaign.stats.opt_outs,
            metadata_=campaign.metadata,
            tags=campaign.tags,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )
    
    async def _update_from_domain(
        self,
        model: CampaignModel,
        campaign: Campaign,
    ) -> None:
        """
        Update ORM model from domain entity
        
        Args:
            model: Existing ORM model
            campaign: Updated domain entity
        """
        model.name = campaign.name
        model.status = campaign.status.value
        model.campaign_type = campaign.campaign_type.value
        model.priority = campaign.priority.value
        model.scheduled_for = campaign.scheduled_for
        model.enable_fallback = campaign.enable_fallback
        model.fallback_channel = campaign.fallback_channel
        model.rate_limit = campaign.rate_limit
        model.recipient_count = campaign.recipient_count
        model.messages_sent = campaign.stats.messages_sent
        model.messages_delivered = campaign.stats.messages_delivered
        model.messages_failed = campaign.stats.messages_failed
        model.messages_read = campaign.stats.messages_read
        model.fallback_triggered = campaign.stats.fallback_triggered
        model.opt_outs = campaign.stats.opt_outs
        model.metadata_ = campaign.metadata
        model.tags = campaign.tags
        model.updated_at = datetime.utcnow()
