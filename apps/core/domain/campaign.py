"""
Campaign Domain Model

This module contains the core business logic for RCS campaigns.
It implements a state machine pattern for campaign lifecycle management
and enforces all business rules without external dependencies.

Domain Events:
    - CampaignCreated
    - CampaignScheduled
    - CampaignStarted
    - CampaignCompleted
    - CampaignCancelled

State Transitions:
    DRAFT -> SCHEDULED -> ACTIVE -> COMPLETED
                      -> CANCELLED
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class CampaignStatus(str, Enum):
    """Campaign lifecycle states"""
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CampaignType(str, Enum):
    """Types of campaigns supported"""
    PROMOTIONAL = "promotional"
    TRANSACTIONAL = "transactional"
    REMINDER = "reminder"
    NOTIFICATION = "notification"


class Priority(str, Enum):
    """Message priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class CampaignStats:
    """Campaign delivery statistics"""
    total_recipients: int = 0
    messages_sent: int = 0
    messages_delivered: int = 0
    messages_failed: int = 0
    messages_read: int = 0
    fallback_triggered: int = 0
    opt_outs: int = 0
    
    @property
    def delivery_rate(self) -> float:
        """Calculate delivery success rate"""
        if self.total_recipients == 0:
            return 0.0
        return (self.messages_delivered / self.total_recipients) * 100
    
    @property
    def read_rate(self) -> float:
        """Calculate read rate"""
        if self.messages_delivered == 0:
            return 0.0
        return (self.messages_read / self.messages_delivered) * 100


@dataclass
class DomainEvent:
    """Base class for domain events"""
    event_id: UUID = field(default_factory=uuid4)
    event_type: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    aggregate_id: UUID = None
    data: Dict[str, Any] = field(default_factory=dict)


class Campaign:
    """
    Campaign Aggregate Root
    
    Represents an RCS messaging campaign with complete lifecycle management.
    Implements invariants and state transitions following DDD principles.
    
    Business Rules:
        1. Campaign must have at least one recipient
        2. Scheduled time must be in the future
        3. Active campaigns can be paused but not edited
        4. Completed campaigns are immutable
        5. Template must be approved before campaign activation
    
    Example:
        >>> campaign = Campaign.create(
        ...     name="Black Friday Sale",
        ...     tenant_id=tenant_id,
        ...     template_id=template_id,
        ...     campaign_type=CampaignType.PROMOTIONAL
        ... )
        >>> campaign.schedule(scheduled_for=datetime(2024, 11, 29, 9, 0))
        >>> campaign.activate()
    """
    
    def __init__(
        self,
        id: UUID,
        tenant_id: UUID,
        name: str,
        campaign_type: CampaignType,
        template_id: UUID,
        status: CampaignStatus = CampaignStatus.DRAFT,
        priority: Priority = Priority.MEDIUM,
        scheduled_for: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ):
        self.id = id
        self.tenant_id = tenant_id
        self.name = name
        self.campaign_type = campaign_type
        self.template_id = template_id
        self.status = status
        self.priority = priority
        self.scheduled_for = scheduled_for
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)
        
        # Audience and targeting
        self.audience_ids: List[UUID] = []
        self.recipient_count: int = 0
        
        # Delivery configuration
        self.enable_fallback: bool = True
        self.fallback_channel: str = "sms"
        self.rate_limit: Optional[int] = None  # messages per second
        
        # Statistics
        self.stats = CampaignStats()
        
        # Metadata
        self.metadata: Dict[str, Any] = {}
        self.tags: List[str] = []
        
        # Domain events (for event sourcing)
        self._events: List[DomainEvent] = []
    
    @classmethod
    def create(
        cls,
        name: str,
        tenant_id: UUID,
        template_id: UUID,
        campaign_type: CampaignType,
        priority: Priority = Priority.MEDIUM,
    ) -> "Campaign":
        """
        Factory method to create a new campaign
        
        Args:
            name: Campaign display name
            tenant_id: Tenant identifier for multi-tenancy
            template_id: Reference to approved template
            campaign_type: Type of campaign (promotional, transactional, etc.)
            priority: Message priority level
            
        Returns:
            New Campaign instance in DRAFT status
            
        Raises:
            ValueError: If name is empty or template_id is invalid
        """
        if not name or not name.strip():
            raise ValueError("Campaign name cannot be empty")
        
        campaign = cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name.strip(),
            campaign_type=campaign_type,
            template_id=template_id,
            priority=priority,
            status=CampaignStatus.DRAFT,
        )
        
        campaign._add_event(
            event_type="campaign.created",
            data={
                "campaign_id": str(campaign.id),
                "name": campaign.name,
                "type": campaign.campaign_type.value,
            }
        )
        
        return campaign
    
    def schedule(self, scheduled_for: datetime) -> None:
        """
        Schedule campaign for future execution
        
        Args:
            scheduled_for: Future datetime for campaign execution
            
        Raises:
            ValueError: If scheduled time is in the past
            InvalidStateTransition: If campaign is not in DRAFT status
        """
        self._ensure_status(CampaignStatus.DRAFT)
        
        if scheduled_for <= datetime.now(timezone.utc):
            raise ValueError("Scheduled time must be in the future")
        
        self.scheduled_for = scheduled_for
        self.status = CampaignStatus.SCHEDULED
        self.updated_at = datetime.now(timezone.utc)
        
        self._add_event(
            event_type="campaign.scheduled",
            data={"scheduled_for": scheduled_for.isoformat()}
        )
    
    def activate(self) -> None:
        """
        Activate campaign for immediate or scheduled execution
        
        Raises:
            InvalidStateTransition: If campaign cannot be activated
            ValueError: If campaign has no recipients
        """
        if self.status not in [CampaignStatus.DRAFT, CampaignStatus.SCHEDULED]:
            raise InvalidStateTransition(
                f"Cannot activate campaign in {self.status} status"
            )
        
        if self.recipient_count == 0:
            raise ValueError("Campaign must have at least one recipient")
        
        self.status = CampaignStatus.ACTIVE
        self.updated_at = datetime.now(timezone.utc)
        
        self._add_event(
            event_type="campaign.activated",
            data={"recipient_count": self.recipient_count}
        )
    
    def pause(self) -> None:
        """Pause an active campaign"""
        self._ensure_status(CampaignStatus.ACTIVE)
        
        self.status = CampaignStatus.PAUSED
        self.updated_at = datetime.now(timezone.utc)
        
        self._add_event(event_type="campaign.paused", data={})
    
    def resume(self) -> None:
        """Resume a paused campaign"""
        self._ensure_status(CampaignStatus.PAUSED)
        
        self.status = CampaignStatus.ACTIVE
        self.updated_at = datetime.now(timezone.utc)
        
        self._add_event(event_type="campaign.resumed", data={})
    
    def complete(self) -> None:
        """Mark campaign as completed"""
        if self.status not in [CampaignStatus.ACTIVE, CampaignStatus.PAUSED]:
            raise InvalidStateTransition(
                f"Cannot complete campaign in {self.status} status"
            )
        
        self.status = CampaignStatus.COMPLETED
        self.updated_at = datetime.now(timezone.utc)
        
        self._add_event(
            event_type="campaign.completed",
            data={
                "stats": {
                    "sent": self.stats.messages_sent,
                    "delivered": self.stats.messages_delivered,
                    "failed": self.stats.messages_failed,
                    "delivery_rate": self.stats.delivery_rate,
                }
            }
        )
    
    def cancel(self, reason: str) -> None:
        """
        Cancel a campaign
        
        Args:
            reason: Reason for cancellation
        """
        if self.status in [CampaignStatus.COMPLETED, CampaignStatus.CANCELLED]:
            raise InvalidStateTransition(
                f"Cannot cancel campaign in {self.status} status"
            )
        
        self.status = CampaignStatus.CANCELLED
        self.updated_at = datetime.now(timezone.utc)
        self.metadata["cancellation_reason"] = reason
        
        self._add_event(
            event_type="campaign.cancelled",
            data={"reason": reason}
        )
    
    def add_audience(self, audience_id: UUID, recipient_count: int) -> None:
        """
        Add an audience segment to the campaign
        
        Args:
            audience_id: ID of the audience segment
            recipient_count: Number of recipients in this segment
        """
        if self.status not in [CampaignStatus.DRAFT]:
            raise InvalidStateTransition(
                "Cannot modify audience of active campaign"
            )
        
        if audience_id not in self.audience_ids:
            self.audience_ids.append(audience_id)
            self.recipient_count += recipient_count
            self.stats.total_recipients = self.recipient_count
            self.updated_at = datetime.now(timezone.utc)
            
            # Sync audience_ids to metadata for orchestrator worker
            self.metadata["audience_ids"] = [str(aid) for aid in self.audience_ids]
    
    def update_stats_from_db(self, stats_data: Dict[str, Any]) -> bool:
        """
        Update campaign statistics from database aggregation.
        Returns True if campaign transitions to COMPLETED.
        
        Terminal states: SENT, DELIVERED, READ, FAILED
        Non-terminal: PENDING, QUEUED
        
        Campaign completes when:
        - Status is ACTIVE
        - has_pending = False (all messages in terminal states)
        - recipient_count > 0
        
        Args:
            stats_data: Dict with keys:
                - messages_sent: int (SENT or higher)
                - messages_delivered: int (DELIVERED or READ)
                - messages_failed: int (terminal FAILED)
                - messages_read: int (READ)
                - fallback_triggered: int
                - has_pending: bool (True if any PENDING/QUEUED)
        
        Returns:
            True if campaign completed, False otherwise
        """
        self.stats.messages_sent = stats_data.get("messages_sent", 0)
        self.stats.messages_delivered = stats_data.get("messages_delivered", 0)
        self.stats.messages_failed = stats_data.get("messages_failed", 0)
        self.stats.messages_read = stats_data.get("messages_read", 0)
        self.stats.fallback_triggered = stats_data.get("fallback_triggered", 0)
        self.updated_at = datetime.now(timezone.utc)
        
        # Completion check: zero pending messages = all terminal
        has_pending = stats_data.get("has_pending", True)
        is_active = self.status == CampaignStatus.ACTIVE
        all_terminal = not has_pending
        
        if is_active and all_terminal and self.recipient_count > 0:
            self.status = CampaignStatus.COMPLETED
            self._add_event(
                event_type="campaign.completed",
                data={
                    "stats": {
                        "sent": self.stats.messages_sent,
                        "delivered": self.stats.messages_delivered,
                        "failed": self.stats.messages_failed,
                        "delivery_rate": self.stats.delivery_rate,
                    }
                }
            )
            return True
        
        return False
    
    def is_active(self) -> bool:
        """Check if campaign is currently active"""
        return self.status == CampaignStatus.ACTIVE
    
    def is_completed(self) -> bool:
        """Check if campaign is completed"""
        return self.status == CampaignStatus.COMPLETED
    
    def can_be_modified(self) -> bool:
        """Check if campaign configuration can be modified"""
        return self.status in [CampaignStatus.DRAFT, CampaignStatus.SCHEDULED]
    
    def _ensure_status(self, expected_status: CampaignStatus) -> None:
        """Ensure campaign is in expected status"""
        if self.status != expected_status:
            raise InvalidStateTransition(
                f"Expected status {expected_status}, got {self.status}"
            )
    
    def _add_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Add domain event to event queue"""
        event = DomainEvent(
            event_type=event_type,
            aggregate_id=self.id,
            data=data,
        )
        self._events.append(event)
    
    def collect_events(self) -> List[DomainEvent]:
        """Collect and clear pending domain events"""
        events = self._events.copy()
        self._events.clear()
        return events
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize campaign to dictionary"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "campaign_type": self.campaign_type.value,
            "template_id": str(self.template_id),
            "status": self.status.value,
            "priority": self.priority.value,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "audience_ids": [str(aid) for aid in self.audience_ids],
            "recipient_count": self.recipient_count,
            "enable_fallback": self.enable_fallback,
            "fallback_channel": self.fallback_channel,
            "rate_limit": self.rate_limit,
            "stats": {
                "total_recipients": self.stats.total_recipients,
                "messages_sent": self.stats.messages_sent,
                "messages_delivered": self.stats.messages_delivered,
                "messages_failed": self.stats.messages_failed,
                "messages_read": self.stats.messages_read,
                "fallback_triggered": self.stats.fallback_triggered,
                "opt_outs": self.stats.opt_outs,
                "delivery_rate": self.stats.delivery_rate,
                "read_rate": self.stats.read_rate,
            },
            "metadata": self.metadata,
            "tags": self.tags,
        }


class InvalidStateTransition(Exception):
    """Raised when an invalid state transition is attempted"""
    pass
