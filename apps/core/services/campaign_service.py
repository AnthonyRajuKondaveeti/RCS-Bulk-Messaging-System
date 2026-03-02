"""
Campaign Service

Orchestrates campaign lifecycle including creation, scheduling,
execution, and statistics tracking.

Responsibilities:
    - Campaign CRUD operations
    - Campaign state management
    - Audience expansion
    - Message generation from templates
    - Campaign statistics aggregation

Dependencies:
    - CampaignRepository
    - MessageRepository
    - QueuePort (for job enqueueing)
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from apps.core.domain.campaign import (
    Campaign,
    CampaignStatus,
    CampaignType,
    Priority,
    InvalidStateTransition,
)
from apps.core.domain.message import Message, MessageContent
from apps.core.ports.repository import (
    CampaignRepository,
    MessageRepository,
    EventRepository,
    UnitOfWork,
)
from apps.core.ports.queue import QueuePort, QueueMessage, QueuePriority


logger = logging.getLogger(__name__)


class CampaignService:
    """
    Campaign orchestration service
    
    Implements business logic for campaign management following
    Domain-Driven Design principles.
    
    Example:
        >>> service = CampaignService(uow, queue)
        >>> campaign = await service.create_campaign(
        ...     tenant_id=tenant_id,
        ...     name="Black Friday Sale",
        ...     template_id=template_id,
        ...     campaign_type=CampaignType.PROMOTIONAL,
        ... )
        >>> await service.schedule_campaign(campaign.id, datetime(2024, 11, 29))
    """
    
    def __init__(
        self,
        uow: UnitOfWork,
        queue: QueuePort,
    ):
        """
        Initialize campaign service
        
        Args:
            uow: Unit of Work for transaction management
            queue: Message queue for async processing
        """
        self.uow = uow
        self.queue = queue
    
    async def create_campaign(
        self,
        tenant_id: UUID,
        name: str,
        template_id: UUID,
        campaign_type: CampaignType,
        priority: Priority = Priority.MEDIUM,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Campaign:
        """
        Create a new campaign
        
        Args:
            tenant_id: Tenant identifier
            name: Campaign name
            template_id: Message template ID
            campaign_type: Type of campaign
            priority: Message priority
            metadata: Additional metadata
            
        Returns:
            Created campaign
            
        Raises:
            ValueError: If template doesn't exist
        """
        async with self.uow:
            # Create campaign
            campaign = Campaign.create(
                name=name,
                tenant_id=tenant_id,
                template_id=template_id,
                campaign_type=campaign_type,
                priority=priority,
            )
            
            if metadata:
                campaign.metadata.update(metadata)
            
            # Persist
            campaign = await self.uow.campaigns.save(campaign)
            
            # Save domain events
            await self._publish_events(campaign)
            
            logger.info(
                f"Campaign created: {campaign.id} (tenant={tenant_id}, name={name})"
            )
            
            return campaign
    
    async def add_audience(
        self,
        campaign_id: UUID,
        audience_id: UUID,
        recipient_phones: List[str],
    ) -> Campaign:
        """
        Add audience segment to campaign
        
        Args:
            campaign_id: Campaign identifier
            audience_id: Audience segment ID
            recipient_phones: List of phone numbers
            
        Returns:
            Updated campaign
            
        Raises:
            EntityNotFoundException: If campaign not found
            InvalidStateTransition: If campaign is active
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Add audience
            campaign.add_audience(audience_id, len(recipient_phones))
            
            # Persist
            campaign = await self.uow.campaigns.save(campaign)
            
            logger.info(
                f"Audience {audience_id} added to campaign {campaign_id} "
                f"({len(recipient_phones)} recipients)"
            )
            
            return campaign
    
    async def schedule_campaign(
        self,
        campaign_id: UUID,
        scheduled_for: datetime,
    ) -> Campaign:
        """
        Schedule campaign for future execution
        
        Args:
            campaign_id: Campaign identifier
            scheduled_for: Execution datetime
            
        Returns:
            Scheduled campaign
            
        Raises:
            ValueError: If scheduled time is invalid
            InvalidStateTransition: If campaign cannot be scheduled
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Schedule
            campaign.schedule(scheduled_for)
            
            # Persist
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            # Enqueue orchestration job
            await self.queue.schedule(
                message=QueueMessage(
                    id=f"campaign-{campaign_id}",
                    queue_name="campaign.orchestrator",
                    payload={"campaign_id": str(campaign_id)},
                    priority=self._map_priority(campaign.priority),
                ),
                scheduled_for=scheduled_for,
            )
            
            logger.info(
                f"Campaign {campaign_id} scheduled for {scheduled_for}"
            )
            
            return campaign
    
    async def activate_campaign(
        self,
        campaign_id: UUID,
    ) -> Campaign:
        """
        Activate campaign for immediate execution
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Activated campaign
            
        Raises:
            InvalidStateTransition: If campaign cannot be activated
            ValueError: If campaign has no recipients
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            # Activate
            campaign.activate()
            
            # Persist
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            # Enqueue orchestration job immediately
            await self.queue.enqueue(
                QueueMessage(
                    id=f"campaign-{campaign_id}",
                    queue_name="campaign.orchestrator",
                    payload={"campaign_id": str(campaign_id)},
                    priority=self._map_priority(campaign.priority),
                )
            )
            
            logger.info(f"Campaign {campaign_id} activated")
            
            return campaign
    
    async def pause_campaign(
        self,
        campaign_id: UUID,
    ) -> Campaign:
        """
        Pause an active campaign
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Paused campaign
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            campaign.pause()
            
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            logger.info(f"Campaign {campaign_id} paused")
            
            return campaign
    
    async def resume_campaign(
        self,
        campaign_id: UUID,
    ) -> Campaign:
        """
        Resume a paused campaign
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Resumed campaign
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            campaign.resume()
            
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            logger.info(f"Campaign {campaign_id} resumed")
            
            return campaign
    
    async def cancel_campaign(
        self,
        campaign_id: UUID,
        reason: str,
    ) -> Campaign:
        """
        Cancel a campaign
        
        Args:
            campaign_id: Campaign identifier
            reason: Cancellation reason
            
        Returns:
            Cancelled campaign
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            campaign.cancel(reason)
            
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            logger.info(f"Campaign {campaign_id} cancelled: {reason}")
            
            return campaign
    
    async def get_campaign(
        self,
        campaign_id: UUID,
    ) -> Optional[Campaign]:
        """
        Get campaign by ID
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Campaign or None if not found
        """
        return await self.uow.campaigns.get_by_id(campaign_id)
    
    async def list_campaigns(
        self,
        tenant_id: UUID,
        status: Optional[CampaignStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Campaign]:
        """
        List campaigns for a tenant
        
        Args:
            tenant_id: Tenant identifier
            status: Optional status filter
            limit: Max results
            offset: Pagination offset
            
        Returns:
            List of campaigns
        """
        return await self.uow.campaigns.get_by_tenant(
            tenant_id=tenant_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    
    async def update_campaign_stats(
        self,
        campaign_id: UUID,
        stats_update: Dict[str, int],
    ) -> None:
        """
        Update campaign statistics
        
        Args:
            campaign_id: Campaign identifier
            stats_update: Statistics to increment
            
        Example:
            >>> await service.update_campaign_stats(
            ...     campaign_id,
            ...     {"messages_sent": 1, "messages_delivered": 1}
            ... )
        """
        async with self.uow:
            await self.uow.campaigns.update_stats(campaign_id, stats_update)
    
    async def complete_campaign(
        self,
        campaign_id: UUID,
    ) -> Campaign:
        """
        Mark campaign as completed
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Completed campaign
        """
        async with self.uow:
            campaign = await self.uow.campaigns.get_by_id(campaign_id)
            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")
            
            campaign.complete()
            
            campaign = await self.uow.campaigns.save(campaign)
            await self._publish_events(campaign)
            
            logger.info(
                f"Campaign {campaign_id} completed - "
                f"Sent: {campaign.stats.messages_sent}, "
                f"Delivered: {campaign.stats.messages_delivered}, "
                f"Rate: {campaign.stats.delivery_rate:.2f}%"
            )
            
            return campaign
    
    async def _publish_events(self, campaign: Campaign) -> None:
        """
        Publish domain events to event store
        
        Args:
            campaign: Campaign with pending events
        """
        events = campaign.collect_events()
        
        for event in events:
            await self.uow.events.save_event(
                event_type=event.event_type,
                aggregate_id=event.aggregate_id,
                aggregate_type="campaign",
                data=event.data,
            )
    
    def _map_priority(self, priority: Priority) -> QueuePriority:
        """Map campaign priority to queue priority"""
        mapping = {
            Priority.LOW: QueuePriority.LOW,
            Priority.MEDIUM: QueuePriority.MEDIUM,
            Priority.HIGH: QueuePriority.HIGH,
            Priority.URGENT: QueuePriority.URGENT,
        }
        return mapping.get(priority, QueuePriority.MEDIUM)
