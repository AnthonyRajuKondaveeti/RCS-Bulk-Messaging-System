"""
Unit of Work Implementation

Manages database transactions and coordinates repositories.
Implements the Unit of Work pattern for atomic operations.

Features:
    - Transaction management
    - Repository coordination
    - Automatic rollback on errors
    - Context manager support
"""

from typing import Optional
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.ports.repository import (
    UnitOfWork,
    CampaignRepository,
    MessageRepository,
    EventRepository,
    OptOutRepository,
    TemplateRepository,
)
from apps.adapters.db.repositories.campaign_repo import SQLAlchemyCampaignRepository
from apps.adapters.db.repositories.message_repo import SQLAlchemyMessageRepository
from apps.adapters.db.repositories.event_repo import SQLAlchemyEventRepository
from apps.adapters.db.repositories.opt_out_repo import SQLAlchemyOptOutRepository
from apps.adapters.db.repositories.template_repo import SQLAlchemyTemplateRepository


logger = logging.getLogger(__name__)


class SQLAlchemyUnitOfWork(UnitOfWork):
    """
    SQLAlchemy Unit of Work implementation
    
    Coordinates multiple repositories in a single transaction.
    Ensures atomic operations across domain aggregates.
    
    Example:
        >>> async with uow:
        ...     campaign = await uow.campaigns.get_by_id(campaign_id)
        ...     campaign.activate()
        ...     await uow.campaigns.save(campaign)
        ...     await uow.commit()
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize Unit of Work
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
        self._campaigns: Optional[CampaignRepository] = None
        self._messages: Optional[MessageRepository] = None
        self._events: Optional[EventRepository] = None
        self._opt_outs: Optional[OptOutRepository] = None
        self._templates: Optional[TemplateRepository] = None
        self._audiences: Optional["AudienceRepository"] = None
        self._audiences: Optional["AudienceRepository"] = None
    
    async def __aenter__(self):
        """Begin transaction if not already in one"""
        if not self.session.in_transaction():
            await self.session.begin()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Commit or rollback transaction"""
        if exc_type is not None:
            # Exception occurred, rollback
            await self.rollback()
            logger.error(f"Transaction rolled back due to: {exc_type.__name__}")
        else:
            # No exception, commit
            try:
                await self.commit()
            except Exception as e:
                logger.exception("Commit failed, rolling back")
                await self.rollback()
                raise
    
    async def commit(self) -> None:
        """Commit transaction"""
        if self.session.in_transaction():
            try:
                await self.session.commit()
                logger.debug("Transaction committed")
            except Exception as e:
                logger.error(f"Commit failed: {e}")
                raise
        else:
            logger.debug("No active transaction to commit")
    
    async def rollback(self) -> None:
        """Rollback transaction"""
        if self.session.in_transaction():
            try:
                await self.session.rollback()
                logger.debug("Transaction rolled back")
            except Exception as e:
                logger.error(f"Rollback failed: {e}")
                raise
        else:
            logger.debug("No active transaction to rollback")
    
    @property
    def campaigns(self) -> CampaignRepository:
        """Get campaign repository"""
        if self._campaigns is None:
            self._campaigns = SQLAlchemyCampaignRepository(self.session)
        return self._campaigns
    
    @property
    def messages(self) -> MessageRepository:
        """Get message repository"""
        if self._messages is None:
            self._messages = SQLAlchemyMessageRepository(self.session)
        return self._messages
    
    @property
    def events(self) -> EventRepository:
        """Get event repository"""
        if self._events is None:
            self._events = SQLAlchemyEventRepository(self.session)
        return self._events
    
    @property
    def opt_outs(self) -> OptOutRepository:
        """Get opt-out repository"""
        if self._opt_outs is None:
            self._opt_outs = SQLAlchemyOptOutRepository(self.session)
        return self._opt_outs
    
    @property
    def templates(self) -> TemplateRepository:
        """Get template repository"""
        if self._templates is None:
            self._templates = SQLAlchemyTemplateRepository(self.session)
        return self._templates
    
    @property
    def audiences(self) -> "AudienceRepository":
        """Get audience repository"""
        if self._audiences is None:
            from apps.adapters.db.repositories.audience_repo import AudienceRepository
            self._audiences = AudienceRepository(self.session)
        return self._audiences

