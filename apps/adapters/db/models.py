"""
SQLAlchemy ORM Models

Database table definitions using SQLAlchemy ORM.
Maps domain models to database tables.

Tables:
    - campaigns
    - messages
    - templates
    - opt_ins
    - events (for event sourcing)
"""

from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    JSON,
    ForeignKey,
    Index,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.adapters.db.postgres import Base
from apps.core.domain.campaign import CampaignStatus, CampaignType, Priority
from apps.core.domain.message import MessageStatus, MessageChannel
from apps.core.domain.opt_in import ConsentStatus


class CampaignModel(Base):
    """Campaign table"""
    
    __tablename__ = "campaigns"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Foreign keys
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    template_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    
    # Basic fields
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum(CampaignStatus, native_enum=False),
        nullable=False,
        default=CampaignStatus.DRAFT,
        index=True,
    )
    campaign_type: Mapped[str] = mapped_column(
        SQLEnum(CampaignType, native_enum=False),
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        SQLEnum(Priority, native_enum=False),
        nullable=False,
        default=Priority.MEDIUM,
    )
    
    # Scheduling
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    
    # Configuration
    enable_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_channel: Mapped[str] = mapped_column(String(20), default="sms")
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Statistics
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    messages_delivered: Mapped[int] = mapped_column(Integer, default=0)
    messages_failed: Mapped[int] = mapped_column(Integer, default=0)
    messages_read: Mapped[int] = mapped_column(Integer, default=0)
    fallback_triggered: Mapped[int] = mapped_column(Integer, default=0)
    opt_outs: Mapped[int] = mapped_column(Integer, default=0)
    
    # Metadata
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    
    # Relationships
    messages: Mapped[List["MessageModel"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
        Index("ix_campaigns_scheduled", "scheduled_for"),
    )


class MessageModel(Base):
    """Message table (high volume - consider partitioning)"""
    
    __tablename__ = "messages"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Foreign keys
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    
    # Recipient
    recipient_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    
    # Status
    status: Mapped[str] = mapped_column(
        SQLEnum(MessageStatus, native_enum=False),
        nullable=False,
        default=MessageStatus.PENDING,
        index=True,
    )
    channel: Mapped[str] = mapped_column(
        SQLEnum(MessageChannel, native_enum=False),
        nullable=False,
        default=MessageChannel.RCS,
    )
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    
    # Content (stored as JSON for flexibility)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    # Delivery tracking
    queued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    
    # Retry and fallback
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Aggregator details
    aggregator: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Metadata
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    
    # Relationships
    campaign: Mapped["CampaignModel"] = relationship(back_populates="messages")
    
    # Indexes
    __table_args__ = (
        Index("ix_messages_status_created", "status", "created_at"),
        Index("ix_messages_tenant_status", "tenant_id", "status"),
    )


class TemplateModel(Base):
    """Template table"""
    
    __tablename__ = "templates"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Foreign keys
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    
    # Basic fields
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Template configuration
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rich_card_template: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    suggestions_template: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    
    # Metadata
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(10), default="en")
    
    # Usage stats
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_templates_tenant_status", "tenant_id", "status"),
    )


class OptInModel(Base):
    """Opt-in/consent table"""
    
    __tablename__ = "opt_ins"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Foreign keys
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    
    # Phone number
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    
    # Consent status
    promotional_status: Mapped[str] = mapped_column(
        SQLEnum(ConsentStatus, native_enum=False),
        nullable=False,
        default=ConsentStatus.OPTED_OUT,
    )
    transactional_status: Mapped[str] = mapped_column(
        SQLEnum(ConsentStatus, native_enum=False),
        nullable=False,
        default=ConsentStatus.OPTED_IN,
    )
    informational_status: Mapped[str] = mapped_column(
        SQLEnum(ConsentStatus, native_enum=False),
        nullable=False,
        default=ConsentStatus.OPTED_OUT,
    )
    
    # Timestamps
    promotional_opted_in_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    promotional_opted_out_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    # DND Registry
    is_on_dnd_registry: Mapped[bool] = mapped_column(Boolean, default=False)
    dnd_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    # Consent history (stored as JSON array)
    consent_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    
    # Preferences
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    
    # Indexes
    __table_args__ = (
        Index("ix_opt_ins_tenant_phone", "tenant_id", "phone_number", unique=True),
    )


class EventModel(Base):
    """Event store table (for event sourcing)"""
    
    __tablename__ = "events"
    
    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    
    # Event details
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    # Event data
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    
    # Indexes
    __table_args__ = (
        Index("ix_events_aggregate", "aggregate_id", "version"),
        Index("ix_events_type_created", "event_type", "created_at"),
    )


class AudienceModel(Base):
    """
    Audience/Contact List Model
    
    Stores collections of contacts for targeting campaigns.
    Supports both static lists (CSV uploads) and dynamic queries.
    """
    __tablename__ = "audiences"
    
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    
    # Basic info
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    audience_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="static",
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Tags for organization
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    
    # For dynamic audiences
    query: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Contacts (for static audiences)
    contacts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    
    # Statistics
    total_contacts: Mapped[int] = mapped_column(Integer, default=0)
    valid_contacts: Mapped[int] = mapped_column(Integer, default=0)
    invalid_contacts: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('idx_audiences_tenant_status', 'tenant_id', 'status'),
        Index('idx_audiences_tenant_type', 'tenant_id', 'audience_type'),
    )

