"""
Event Repository Implementation

Stores domain events for event sourcing and audit trail.
Supports event replay and aggregate reconstruction.

Features:
    - Event persistence
    - Event ordering by version
    - Event retrieval by aggregate
    - Event type queries
"""

from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4
from datetime import datetime
import logging

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.ports.repository import EventRepository
from apps.adapters.db.models import EventModel


logger = logging.getLogger(__name__)


class SQLAlchemyEventRepository(EventRepository):
    """
    SQLAlchemy implementation of Event Repository
    
    Stores domain events in append-only log.
    
    Example:
        >>> repo = SQLAlchemyEventRepository(session)
        >>> event_id = await repo.save_event(
        ...     event_type="campaign.created",
        ...     aggregate_id=campaign_id,
        ...     aggregate_type="campaign",
        ...     data={"name": "Black Friday"},
        ... )
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize repository
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
    
    async def save_event(
        self,
        event_type: str,
        aggregate_id: UUID,
        aggregate_type: str,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UUID:
        """
        Save domain event
        
        Args:
            event_type: Type of event (e.g., "campaign.created")
            aggregate_id: ID of aggregate root
            aggregate_type: Type of aggregate (e.g., "campaign")
            data: Event data
            metadata: Additional metadata
            
        Returns:
            Event ID
        """
        # Get next version number for this aggregate
        stmt = select(EventModel).where(
            EventModel.aggregate_id == aggregate_id
        ).order_by(EventModel.version.desc())
        
        result = await self.session.execute(stmt)
        last_event = result.scalar_one_or_none()
        
        version = 1 if last_event is None else last_event.version + 1
        
        # Create event
        event = EventModel(
            id=uuid4(),
            event_type=event_type,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            version=version,
            data=data,
            metadata_=metadata or {},
            created_at=datetime.utcnow(),
        )
        
        self.session.add(event)
        await self.session.flush()
        
        logger.debug(
            f"Saved event {event_type} for {aggregate_type}:{aggregate_id} "
            f"(version {version})"
        )
        
        return event.id
    
    async def get_events(
        self,
        aggregate_id: UUID,
        from_version: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get events for an aggregate
        
        Args:
            aggregate_id: Aggregate identifier
            from_version: Start from this version
            
        Returns:
            List of events in order
        """
        stmt = (
            select(EventModel)
            .where(
                and_(
                    EventModel.aggregate_id == aggregate_id,
                    EventModel.version > from_version,
                )
            )
            .order_by(EventModel.version)
        )
        
        result = await self.session.execute(stmt)
        events = result.scalars().all()
        
        return [self._to_dict(event) for event in events]
    
    async def get_events_by_type(
        self,
        event_type: str,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get events by type
        
        Args:
            event_type: Event type to filter
            since: Get events after this time
            limit: Max results
            
        Returns:
            List of matching events
        """
        stmt = select(EventModel).where(
            EventModel.event_type == event_type
        )
        
        if since:
            stmt = stmt.where(EventModel.created_at > since)
        
        stmt = stmt.order_by(EventModel.created_at.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        events = result.scalars().all()
        
        return [self._to_dict(event) for event in events]
    
    def _to_dict(self, event: EventModel) -> Dict[str, Any]:
        """Convert event model to dictionary"""
        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "aggregate_id": str(event.aggregate_id),
            "aggregate_type": event.aggregate_type,
            "version": event.version,
            "data": event.data,
            "metadata": event.metadata_,
            "created_at": event.created_at.isoformat(),
        }
