"""
SQLAlchemy ORM Models

Database table definitions using SQLAlchemy ORM.
Maps domain models to database tables.

Key change vs original:
    TemplateModel now has external_template_id — the rcssms.in approved
    template ID string (e.g. "7U5QvSVi5e") returned after template approval.
    This is required for sending messages.
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
    UniqueConstraint,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.adapters.db.postgres import Base
from apps.core.domain.campaign import CampaignStatus, CampaignType, Priority
from apps.core.domain.message import MessageStatus, MessageChannel
from apps.core.domain.opt_in import ConsentStatus


class CampaignModel(Base):
    """Campaign table"""

    __tablename__ = "campaigns"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    template_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum(CampaignStatus, native_enum=False),
        nullable=False,
        default=CampaignStatus.DRAFT,
        index=True,
    )
    campaign_type: Mapped[str] = mapped_column(
        SQLEnum(CampaignType, native_enum=False), nullable=False
    )
    priority: Mapped[str] = mapped_column(
        SQLEnum(Priority, native_enum=False), nullable=False, default=Priority.MEDIUM
    )

    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    enable_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_channel: Mapped[str] = mapped_column(String(20), default="sms")
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    messages_delivered: Mapped[int] = mapped_column(Integer, default=0)
    messages_failed: Mapped[int] = mapped_column(Integer, default=0)
    messages_read: Mapped[int] = mapped_column(Integer, default=0)
    fallback_triggered: Mapped[int] = mapped_column(Integer, default=0)
    opt_outs: Mapped[int] = mapped_column(Integer, default=0)

    # metadata_ stores audience_ids, description, tags, etc.
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    messages: Mapped[List["MessageModel"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
        Index("ix_campaigns_scheduled", "scheduled_for"),
    )


class MessageModel(Base):
    """Message table (high volume — consider partitioning by created_at)"""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False, index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)

    recipient_phone: Mapped[str] = mapped_column(String(20), nullable=False)

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

    # Content stored as JSON — includes text, template_id, variables, rich_card, suggestions
    content: Mapped[dict] = mapped_column(JSON, nullable=False)

    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_triggered: Mapped[bool] = mapped_column(Boolean, default=False)

    aggregator: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    campaign: Mapped["CampaignModel"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_status_created", "status", "created_at"),
        Index("ix_messages_tenant_status", "tenant_id", "status"),
    )


class TemplateModel(Base):
    """
    Template table.

    KEY FIELD: external_template_id
        The rcssms.in approved template ID string (e.g. "7U5QvSVi5e").
        This is returned by the rcssms.in template approval API and MUST be
        stored here before the template can be used in campaigns.
        Without this, no messages can be sent.
    """

    __tablename__ = "templates"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # rcssms.in external template ID — populated after operator approval
    # e.g. "7U5QvSVi5e" returned by /rcscreatetemplate.jsp
    external_template_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )

    # Template type for rcssms.in: BASIC, RICH, RICHCASOUREL
    rcs_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BASIC")

    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rich_card_template: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    suggestions_template: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(10), default="en")

    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_templates_tenant_status", "tenant_id", "status"),
        Index("ix_templates_external_id", "external_template_id"),
    )


class OptInModel(Base):
    """Opt-in/consent table"""

    __tablename__ = "opt_ins"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)

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

    promotional_opted_in_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promotional_opted_out_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_on_dnd_registry: Mapped[bool] = mapped_column(Boolean, default=False)
    dnd_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    consent_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_opt_ins_tenant_phone", "tenant_id", "phone_number", unique=True),
    )


class EventModel(Base):
    """Event store table (for event sourcing)"""

    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    metadata_: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_events_aggregate", "aggregate_id", "version"),
        Index("ix_events_type_created", "event_type", "created_at"),
    )


class AudienceContactModel(Base):
    """
    Normalised audience contacts table.

    Replaces the old `audiences.contacts` JSON blob.
    One row per (audience, phone_number) — unique constraint prevents duplicates.

    variables: ordered list of template variable values, e.g. ["John", "ORD1234"].
               Must match the order of variables in the associated template.
    """

    __tablename__ = "audience_contacts"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    audience_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audiences.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone_number: Mapped[str] = mapped_column(Text, nullable=False)

    # Ordered list of template variable values for personalisation
    variables: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Arbitrary per-contact metadata (name, email, order ID, …)
    metadata_: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default="now()",
        nullable=False,
    )

    # Back-reference to the owning audience (lazy by default — streaming skips this)
    audience: Mapped["AudienceModel"] = relationship(back_populates="contact_rows")

    __table_args__ = (
        # Primary access pattern: all contacts for a given audience, keyset-paginated by id
        Index("ix_audience_contacts_audience_id", "audience_id"),
        Index("ix_audience_contacts_audience_id_id", "audience_id", "id"),
        # Deduplication: one row per (audience, phone)
        UniqueConstraint("audience_id", "phone_number", name="uq_audience_contact"),
    )


class AudienceModel(Base):
    """
    Audience/Contact List Model.

    IMPORTANT: The `contacts` JSON blob column has been REMOVED.
    Contacts are now stored in the `audience_contacts` table (one row per contact).
    Use AudienceRepository.stream_contacts() to iterate contacts in batches.
    """

    __tablename__ = "audiences"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    audience_type: Mapped[str] = mapped_column(String(50), nullable=False, default="static")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    query: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # contacts column REMOVED — data lives in audience_contacts table

    total_contacts: Mapped[int] = mapped_column(Integer, default=0)
    valid_contacts: Mapped[int] = mapped_column(Integer, default=0)
    invalid_contacts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", onupdate="now()", nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ORM relationship — used for eager loading in tests / small APIs.
    # For high-volume dispatch, use AudienceRepository.stream_contacts() instead.
    contact_rows: Mapped[list["AudienceContactModel"]] = relationship(
        back_populates="audience",
        cascade="all, delete-orphan",
        lazy="select",
    )

    __table_args__ = (
        Index("idx_audiences_tenant_status", "tenant_id", "status"),
        Index("idx_audiences_tenant_type", "tenant_id", "audience_type"),
    )
