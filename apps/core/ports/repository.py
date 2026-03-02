"""
Repository Port Interface

Defines the contract for data persistence layer.
Implements Repository Pattern for domain model persistence.

Implementations:
    - PostgresRepository
    - CampaignRepository
    - MessageRepository
    
Pattern: Repository Pattern + Unit of Work
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, TypeVar, Generic
from uuid import UUID
from datetime import datetime

from apps.core.domain.campaign import Campaign, CampaignStatus
from apps.core.domain.campaign import Campaign, CampaignStatus
from apps.core.domain.message import Message, MessageStatus, MessageChannel
from apps.core.domain.template import Template, TemplateStatus


T = TypeVar('T')


class Repository(ABC, Generic[T]):
    """
    Generic repository interface
    
    Provides CRUD operations for domain entities.
    All repositories must implement this interface.
    """
    
    @abstractmethod
    async def save(self, entity: T) -> T:
        """
        Save entity (insert or update)
        
        Args:
            entity: Entity to persist
            
        Returns:
            Saved entity with updated fields
        """
        pass
    
    @abstractmethod
    async def get_by_id(self, id: UUID) -> Optional[T]:
        """
        Retrieve entity by ID
        
        Args:
            id: Entity identifier
            
        Returns:
            Entity or None if not found
        """
        pass
    
    @abstractmethod
    async def delete(self, id: UUID) -> bool:
        """
        Delete entity
        
        Args:
            id: Entity identifier
            
        Returns:
            True if deleted, False if not found
        """
        pass
    
    @abstractmethod
    async def exists(self, id: UUID) -> bool:
        """
        Check if entity exists
        
        Args:
            id: Entity identifier
            
        Returns:
            True if exists
        """
        pass


class CampaignRepository(Repository[Campaign]):
    """
    Campaign-specific repository operations
    
    Extends base repository with campaign queries.
    """
    
    @abstractmethod
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
            tenant_id: Tenant identifier
            limit: Max results
            offset: Pagination offset
            status: Filter by status
            
        Returns:
            List of campaigns
        """
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
    async def update_stats(
        self,
        campaign_id: UUID,
        stats_update: Dict[str, int],
    ) -> None:
        """
        Update campaign statistics atomically
        
        Args:
            campaign_id: Campaign to update
            stats_update: Stats to increment (e.g., {"messages_sent": 1})
        """
        pass
    
    @abstractmethod
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
            filters: Additional filters (tags, date ranges, etc.)
            
        Returns:
            Matching campaigns
        """
        pass



class TemplateRepository(Repository[Template]):
    """
    Template-specific repository operations
    """
    
    @abstractmethod
    async def get_by_tenant(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[TemplateStatus] = None,
    ) -> List[Template]:
        """
        Get templates for a tenant
        
        Args:
            tenant_id: Tenant identifier
            limit: Max results
            offset: Pagination offset
            status: Filter by status
            
        Returns:
            List of templates
        """
        pass


class MessageRepository(Repository[Message]):
    """
    Message-specific repository operations
    
    Handles high-volume message persistence with optimizations.
    """
    
    @abstractmethod
    async def save_batch(
        self,
        messages: List[Message],
    ) -> List[Message]:
        """
        Bulk save messages for performance
        
        Args:
            messages: Messages to save
            
        Returns:
            Saved messages
        """
        pass
    
    @abstractmethod
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
            campaign_id: Campaign identifier
            limit: Max results
            offset: Pagination offset
            status: Filter by status
            
        Returns:
            List of messages
        """
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
    async def update_status(
        self,
        message_id: UUID,
        status: MessageStatus,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update message status atomically
        
        Args:
            message_id: Message identifier
            status: New status
            metadata: Additional data to update
        """
        pass
    
    @abstractmethod
    async def get_delivery_stats(
        self,
        campaign_id: UUID,
    ) -> Dict[str, int]:
        """
        Get aggregated delivery statistics
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Stats dictionary with counts
        """
        pass


class EventRepository(ABC):
    """
    Repository for domain events (Event Sourcing)
    
    Stores domain events for audit trail and event replay.
    """
    
    @abstractmethod
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
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
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
        pass


class OptOutRepository(ABC):
    """Repository for opt-out/consent management"""
    
    @abstractmethod
    async def is_opted_out(
        self,
        phone_number: str,
        tenant_id: UUID,
    ) -> bool:
        """
        Check if phone number has opted out
        
        Args:
            phone_number: Phone number in E.164
            tenant_id: Tenant context
            
        Returns:
            True if opted out
        """
        pass
    
    @abstractmethod
    async def opt_out(
        self,
        phone_number: str,
        tenant_id: UUID,
        reason: Optional[str] = None,
    ) -> None:
        """
        Record opt-out
        
        Args:
            phone_number: Phone number
            tenant_id: Tenant context
            reason: Opt-out reason
        """
        pass
    
    @abstractmethod
    async def opt_in(
        self,
        phone_number: str,
        tenant_id: UUID,
    ) -> None:
        """
        Record opt-in (re-enable messaging)
        
        Args:
            phone_number: Phone number
            tenant_id: Tenant context
        """
        pass
    
    @abstractmethod
    async def get_opt_outs(
        self,
        tenant_id: UUID,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get opt-out records
        
        Args:
            tenant_id: Tenant context
            since: Get opt-outs after this time
            
        Returns:
            List of opt-out records
        """
        pass


class UnitOfWork(ABC):
    """
    Unit of Work pattern for transaction management
    
    Ensures atomic operations across multiple repositories.
    """
    
    @abstractmethod
    async def __aenter__(self):
        """Begin transaction"""
        pass
    
    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Commit or rollback transaction"""
        pass
    
    @abstractmethod
    async def commit(self) -> None:
        """Commit transaction"""
        pass
    
    @abstractmethod
    async def rollback(self) -> None:
        """Rollback transaction"""
        pass
    
    @property
    @abstractmethod
    def campaigns(self) -> CampaignRepository:
        """Get campaign repository"""
        pass
    
    @property
    @abstractmethod
    def messages(self) -> MessageRepository:
        """Get message repository"""
        pass
    
    @property
    @abstractmethod
    def events(self) -> EventRepository:
        """Get event repository"""
        pass
    
    @property
    @abstractmethod
    def opt_outs(self) -> OptOutRepository:
        """Get opt-out repository"""
        pass
    
    @property
    @abstractmethod
    def templates(self) -> TemplateRepository:
        """Get template repository"""
        pass


class RepositoryException(Exception):
    """Base exception for repository errors"""
    pass


class EntityNotFoundException(RepositoryException):
    """Raised when entity is not found"""
    pass


class ConcurrencyException(RepositoryException):
    """Raised when optimistic locking fails"""
    pass
