"""
Message Domain Model

Represents individual RCS/SMS messages with rich content support,
delivery tracking, and fallback mechanism.

RCS Features:
    - Rich Cards (images, videos, carousels)
    - Suggested Actions (Quick Replies, URLs, Calls)
    - Read Receipts
    - Typing Indicators
    
Delivery Lifecycle:
    PENDING -> QUEUED -> SENT -> DELIVERED -> READ
                     -> FAILED -> FALLBACK_SENT
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class MessageStatus(str, Enum):
    """Message delivery states"""
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    FALLBACK_SENT = "fallback_sent"
    FALLBACK_DELIVERED = "fallback_delivered"
    EXPIRED = "expired"


class MessageChannel(str, Enum):
    """Supported messaging channels"""
    RCS = "rcs"
    SMS = "sms"
    WHATSAPP = "whatsapp"  # Future


class FailureReason(str, Enum):
    """Categorized failure reasons"""
    INVALID_NUMBER = "invalid_number"
    RCS_NOT_SUPPORTED = "rcs_not_supported"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class SuggestedAction:
    """
    RCS Suggested Actions (Quick Replies, URLs, etc.)
    
    Types:
        - REPLY: Quick reply button
        - URL: Open URL button
        - DIAL: Call phone number
        - SHARE_LOCATION: Request location
        - REQUEST_CALENDAR: Calendar event
    """
    type: str  # "reply", "url", "dial", "share_location"
    text: str
    postback_data: Optional[str] = None  # For reply actions
    url: Optional[str] = None  # For URL actions
    phone_number: Optional[str] = None  # For dial actions


@dataclass
class RichCard:
    """
    RCS Rich Card with media and action buttons
    
    Supports:
        - Standalone cards
        - Carousel (multiple cards)
        - Media (images, videos)
        - Action buttons
    """
    title: Optional[str] = None
    description: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None  # "image/jpeg", "video/mp4"
    media_height: str = "MEDIUM"  # SHORT, MEDIUM, TALL
    suggestions: List[SuggestedAction] = field(default_factory=list)


@dataclass
class MessageContent:
    """
    Message content supporting both RCS rich messaging and SMS fallback
    
    For RCS:
        - Rich text with markdown
        - Rich cards with images/videos
        - Suggested actions
        
    For SMS fallback:
        - Plain text only
        - Link extraction from rich cards
    """
    text: str
    rich_card: Optional[RichCard] = None
    suggestions: List[SuggestedAction] = field(default_factory=list)
    
    def to_sms_text(self) -> str:
        """
        Convert rich content to plain SMS text
        
        Returns:
            Plain text suitable for SMS with URLs extracted
        """
        sms_text = self.text
        
        # Append URL from rich card if present
        if self.rich_card and self.rich_card.media_url:
            sms_text += f"\n{self.rich_card.media_url}"
        
        # Append URLs from URL suggestions
        for suggestion in self.suggestions:
            if suggestion.type == "url" and suggestion.url:
                sms_text += f"\n{suggestion.text}: {suggestion.url}"
        
        return sms_text


@dataclass
class DeliveryAttempt:
    """Record of a delivery attempt"""
    attempt_number: int
    channel: MessageChannel
    attempted_at: datetime
    status: MessageStatus
    aggregator: Optional[str] = None  # "gupshup", "route", etc.
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    external_id: Optional[str] = None  # Vendor message ID


    def to_sms_text(self) -> str:
        """
        Convert rich content to plain SMS text
        
        Strips rich card and suggestions, returning plain text
        suitable for SMS fallback.
        
        Returns:
            Plain text version of message
        """
        # Start with main text
        sms_text = self.text
        
        # Add rich card info if present
        if self.rich_card:
            if self.rich_card.title:
                sms_text += f"\n\n{self.rich_card.title}"
            if self.rich_card.description:
                sms_text += f"\n{self.rich_card.description}"
            if self.rich_card.media_url:
                sms_text += f"\nView: {self.rich_card.media_url}"
        
        # Add suggestions as text links
        if self.suggestions:
            sms_text += "\n"
            for suggestion in self.suggestions:
                if suggestion.type == "url" and suggestion.url:
                    sms_text += f"\n{suggestion.text}: {suggestion.url}"
                elif suggestion.type == "dial" and suggestion.phone_number:
                    sms_text += f"\nCall: {suggestion.phone_number}"
                elif suggestion.type == "reply":
                    # Skip reply suggestions for SMS
                    pass
        
        # Truncate to SMS length limit (160 chars for single SMS)
        # Note: Most providers support concatenated SMS up to 1600 chars
        max_length = 1600
        if len(sms_text) > max_length:
            sms_text = sms_text[:max_length-3] + "..."
        
        return sms_text.strip()


class Message:
    """
    Message Aggregate
    
    Represents a single message to be delivered via RCS (with SMS fallback).
    Tracks complete delivery lifecycle and supports retry logic.
    
    Business Rules:
        1. RCS must be attempted before SMS fallback
        2. Max 3 retry attempts per channel
        3. Messages expire after 24 hours
        4. Read receipts only tracked for RCS
    
    Example:
        >>> content = MessageContent(
        ...     text="Your order #1234 has shipped!",
        ...     suggestions=[
        ...         SuggestedAction(type="url", text="Track Order", url="https://...")
        ...     ]
        ... )
        >>> message = Message.create(
        ...     campaign_id=campaign_id,
        ...     recipient_phone="+919876543210",
        ...     content=content
        ... )
    """
    
    def __init__(
        self,
        id: UUID,
        campaign_id: UUID,
        tenant_id: UUID,
        recipient_phone: str,
        content: MessageContent,
        status: MessageStatus = MessageStatus.PENDING,
        channel: MessageChannel = MessageChannel.RCS,
        priority: str = "medium",
        created_at: Optional[datetime] = None,
    ):
        self.id = id
        self.campaign_id = campaign_id
        self.tenant_id = tenant_id
        self.recipient_phone = self._normalize_phone(recipient_phone)
        self.content = content
        self.status = status
        self.channel = channel
        self.priority = priority
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        
        # Delivery tracking
        self.queued_at: Optional[datetime] = None
        self.sent_at: Optional[datetime] = None
        self.delivered_at: Optional[datetime] = None
        self.read_at: Optional[datetime] = None
        self.failed_at: Optional[datetime] = None
        self.expires_at: datetime = self.created_at.replace(hour=23, minute=59)
        
        # Retry and fallback
        self.retry_count: int = 0
        self.max_retries: int = 3
        self.fallback_enabled: bool = True
        self.fallback_triggered: bool = False
        
        # Delivery details
        self.delivery_attempts: List[DeliveryAttempt] = []
        self.aggregator: Optional[str] = None
        self.external_id: Optional[str] = None  # Vendor's message ID
        self.failure_reason: Optional[FailureReason] = None
        
        # Metadata
        self.metadata: Dict[str, Any] = {}
    
    @classmethod
    def create(
        cls,
        campaign_id: UUID,
        tenant_id: UUID,
        recipient_phone: str,
        content: MessageContent,
        priority: str = "medium",
    ) -> "Message":
        """
        Create a new message for delivery
        
        Args:
            campaign_id: Parent campaign ID
            tenant_id: Tenant identifier
            recipient_phone: Recipient phone in E.164 format
            content: Rich message content
            priority: Message priority (low, medium, high, urgent)
            
        Returns:
            New Message instance
        """
        return cls(
            id=uuid4(),
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            recipient_phone=recipient_phone,
            content=content,
            priority=priority,
            channel=MessageChannel.RCS,
            status=MessageStatus.PENDING,
        )
    
    def queue(self) -> None:
        """Mark message as queued for delivery"""
        if self.status != MessageStatus.PENDING:
            raise ValueError(f"Cannot queue message in {self.status} status")
        
        self.status = MessageStatus.QUEUED
        self.queued_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_sent(self, aggregator: str, external_id: str) -> None:
        """
        Mark message as sent to aggregator
        
        Args:
            aggregator: Name of aggregator (gupshup, route, etc.)
            external_id: Vendor's message ID for tracking
        """
        self.status = MessageStatus.SENT
        self.sent_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.aggregator = aggregator
        self.external_id = external_id
        
        # Record delivery attempt
        self._record_attempt(
            channel=self.channel,
            status=MessageStatus.SENT,
            aggregator=aggregator,
            external_id=external_id,
        )
    
    def mark_delivered(self) -> None:
        """Mark message as delivered to recipient"""
        if self.status not in [MessageStatus.SENT, MessageStatus.QUEUED]:
            raise ValueError(
                f"Cannot mark delivered from {self.status} status"
            )
        
        self.status = MessageStatus.DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        
        self._record_attempt(
            channel=self.channel,
            status=MessageStatus.DELIVERED,
            aggregator=self.aggregator,
        )
    
    def mark_read(self) -> None:
        """Mark message as read (RCS only)"""
        if self.channel != MessageChannel.RCS:
            return  # SMS doesn't support read receipts
        
        if self.status != MessageStatus.DELIVERED:
            raise ValueError(f"Cannot mark read from {self.status} status")
        
        self.status = MessageStatus.READ
        self.read_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_failed(
        self,
        reason: FailureReason,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Mark message delivery as failed
        
        Args:
            reason: Categorized failure reason
            error_code: Vendor error code
            error_message: Human-readable error message
        """
        self.status = MessageStatus.FAILED
        self.failed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.failure_reason = reason
        
        self._record_attempt(
            channel=self.channel,
            status=MessageStatus.FAILED,
            aggregator=self.aggregator,
            error_code=error_code,
            error_message=error_message,
        )
    
    def should_retry(self) -> bool:
        """
        Determine if message should be retried
        
        Returns:
            True if retry attempt should be made
        """
        if self.status != MessageStatus.FAILED:
            return False
        
        if self.retry_count >= self.max_retries:
            return False
        
        if datetime.now(timezone.utc) > self.expires_at:
            return False
        
        # Don't retry if number is invalid or RCS not supported
        if self.failure_reason in [
            FailureReason.INVALID_NUMBER,
            FailureReason.BLOCKED,
        ]:
            return False
        
        return True
    
    def should_fallback_to_sms(self) -> bool:
        """
        Determine if SMS fallback should be triggered
        
        Returns:
            True if fallback to SMS should occur
        """
        if not self.fallback_enabled:
            return False
        
        if self.fallback_triggered:
            return False
        
        if self.channel == MessageChannel.SMS:
            return False  # Already on SMS
        
        # Trigger fallback if RCS not supported or max retries exceeded
        if self.failure_reason == FailureReason.RCS_NOT_SUPPORTED:
            return True
        
        if self.retry_count >= self.max_retries:
            return True
        
        return False
    
    def trigger_fallback(self) -> None:
        """Switch to SMS fallback delivery"""
        if not self.should_fallback_to_sms():
            raise ValueError("Cannot trigger fallback for this message")
        
        self.channel = MessageChannel.SMS
        self.status = MessageStatus.PENDING
        self.fallback_triggered = True
        self.retry_count = 0  # Reset retry counter for SMS attempts
        self.updated_at = datetime.now(timezone.utc)
        
        # Convert rich content to plain SMS
        self.metadata["original_channel"] = "rcs"
        self.metadata["sms_text"] = self.content.to_sms_text()
    
    def mark_fallback_sent(self, aggregator: str, external_id: str) -> None:
        """Mark SMS fallback as sent"""
        self.status = MessageStatus.FALLBACK_SENT
        self.sent_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.aggregator = aggregator
        self.external_id = external_id
        
        self._record_attempt(
            channel=MessageChannel.SMS,
            status=MessageStatus.FALLBACK_SENT,
            aggregator=aggregator,
            external_id=external_id,
        )
    
    def mark_fallback_delivered(self) -> None:
        """Mark SMS fallback as delivered"""
        self.status = MessageStatus.FALLBACK_DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
    
    def increment_retry(self) -> None:
        """Increment retry counter"""
        self.retry_count += 1
        self.updated_at = datetime.now(timezone.utc)
    
    def is_delivered(self) -> bool:
        """Check if message was successfully delivered"""
        return self.status in [
            MessageStatus.DELIVERED,
            MessageStatus.READ,
            MessageStatus.FALLBACK_DELIVERED,
        ]
    
    def is_failed(self) -> bool:
        """Check if message permanently failed"""
        return self.status == MessageStatus.FAILED and not self.should_retry()
    
    def is_expired(self) -> bool:
        """Check if message has expired"""
        return datetime.now(timezone.utc) > self.expires_at
    
    def _normalize_phone(self, phone: str) -> str:
        """
        Normalize phone number to E.164 format
        
        Args:
            phone: Phone number in any format
            
        Returns:
            E.164 formatted phone number
        """
        # Remove all non-numeric characters
        digits = ''.join(filter(str.isdigit, phone))
        
        # Add + prefix if not present
        if not phone.startswith('+'):
            # Assume Indian number if 10 digits
            if len(digits) == 10:
                return f"+91{digits}"
            return f"+{digits}"
        
        return phone
    
    def _record_attempt(
        self,
        channel: MessageChannel,
        status: MessageStatus,
        aggregator: Optional[str] = None,
        external_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Record a delivery attempt"""
        attempt = DeliveryAttempt(
            attempt_number=len(self.delivery_attempts) + 1,
            channel=channel,
            attempted_at=datetime.now(timezone.utc),
            status=status,
            aggregator=aggregator,
            error_code=error_code,
            error_message=error_message,
            external_id=external_id,
        )
        self.delivery_attempts.append(attempt)
    
    def mark_fallback_sent(
        self,
        aggregator: str,
        external_id: str,
    ) -> None:
        """
        Mark message as sent via SMS fallback
        
        Args:
            aggregator: Aggregator name that sent SMS
            external_id: External message ID from aggregator
        """
        self.status = MessageStatus.FALLBACK_SENT
        self.fallback_triggered = True
        self.channel = MessageChannel.SMS
        self.aggregator = aggregator
        self.external_id = external_id
        self.sent_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary"""
        return {
            "id": str(self.id),
            "campaign_id": str(self.campaign_id),
            "tenant_id": str(self.tenant_id),
            "recipient_phone": self.recipient_phone,
            "status": self.status.value,
            "channel": self.channel.value,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "retry_count": self.retry_count,
            "fallback_triggered": self.fallback_triggered,
            "aggregator": self.aggregator,
            "external_id": self.external_id,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "delivery_attempts": len(self.delivery_attempts),
            "metadata": self.metadata,
        }
