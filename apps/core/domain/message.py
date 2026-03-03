"""
Message Domain Model - Production Grade

MAJOR REFACTORING (2026-03-03):
  - Enforced strict state machine (FAILED never transitions to PENDING)
  - Fallback creates NEW child message instead of modifying parent
  - Added parent_message_id for parent-child linkage
  - Removed FALLBACK_SENT/FALLBACK_DELIVERED statuses (use channel field instead)
  - Eliminates stuck messages by design

State Transitions:
  PENDING → QUEUED → SENT → DELIVERED → READ
  PENDING → QUEUED → SENT → FAILED (terminal)
  PENDING → FAILED (terminal)
  
NEVER ALLOWED:
  FAILED → PENDING
  FAILED → any other status
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class MessageStatus(str, Enum):
    """Message status - enforces linear progression, FAILED is terminal"""
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"        # Terminal state - never transitions out
    EXPIRED = "expired"      # Terminal state


class MessageChannel(str, Enum):
    RCS = "rcs"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class FailureReason(str, Enum):
    INVALID_NUMBER = "invalid_number"
    RCS_NOT_SUPPORTED = "rcs_not_supported"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    EXPIRED = "expired"
    AGGREGATOR_ERROR = "aggregator_error"
    UNKNOWN = "unknown"


@dataclass
class SuggestedAction:
    type: str
    text: str
    postback_data: Optional[str] = None
    url: Optional[str] = None
    phone_number: Optional[str] = None


@dataclass
class RichCard:
    title: Optional[str] = None
    description: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    media_height: str = "MEDIUM"
    suggestions: List[SuggestedAction] = field(default_factory=list)


@dataclass
class MessageContent:
    """
    Message content for RCS delivery via rcssms.in.

    MANDATORY for every RCS send:
        template_id : approved template ID from rcssms.in (e.g. "7U5QvSVi5e")
        variables   : ordered list of variable values matching template placeholders
                      e.g. template "Hi {{1}}, order {{2}}" -> variables=["John","ORD-1"]
        rcs_type    : "BASIC", "RICH", or "RICHCASOUREL" — must match the approved
                      template type registered with rcssms.in. Defaults to "BASIC".

    For SMS fallback, to_sms_text() strips rich content to plain text.
    """
    text: str
    rich_card: Optional[RichCard] = None
    suggestions: List[SuggestedAction] = field(default_factory=list)
    template_id: Optional[str] = None           # rcssms.in approved template ID
    variables: List[Any] = field(default_factory=list)  # ordered variable values
    rcs_type: str = "BASIC"                     # BASIC | RICH | RICHCASOUREL

    def to_sms_text(self) -> str:
        """Convert rich content to plain SMS text (max 1600 chars)."""
        sms_text = self.text
        if self.rich_card:
            if self.rich_card.title:
                sms_text += f"\n\n{self.rich_card.title}"
            if self.rich_card.description:
                sms_text += f"\n{self.rich_card.description}"
            if self.rich_card.media_url:
                sms_text += f"\nView: {self.rich_card.media_url}"
        for s in self.suggestions:
            if s.type == "url" and s.url:
                sms_text += f"\n{s.text}: {s.url}"
            elif s.type == "dial" and s.phone_number:
                sms_text += f"\nCall: {s.phone_number}"
        if len(sms_text) > 1600:
            sms_text = sms_text[:1597] + "..."
        return sms_text.strip()


@dataclass
class DeliveryAttempt:
    attempt_number: int
    channel: MessageChannel
    attempted_at: datetime
    status: MessageStatus
    aggregator: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    external_id: Optional[str] = None


class Message:
    """
    Message Aggregate - One message = One delivery attempt
    
    NEW DESIGN (2026-03-03):
      - One Message represents ONE delivery attempt (RCS or SMS, not both)
      - FAILED status is terminal (never reverts to PENDING)
      - Fallback creates NEW child message with parent_message_id linkage
      - State transitions are strictly validated
    
    Business Rules:
      1. Status transitions must be valid per _VALID_TRANSITIONS
      2. FAILED status never transitions to any other status
      3. Fallback creates new Message (not modifies existing)
      4. Max 3 retries per message
      5. Expires 24 hours after creation
      6. Read receipts only for RCS channel
    """

    # Valid state transitions - FAILED has no outgoing transitions
    _VALID_TRANSITIONS = {
        MessageStatus.PENDING: [MessageStatus.QUEUED, MessageStatus.FAILED, MessageStatus.EXPIRED],
        MessageStatus.QUEUED: [MessageStatus.SENT, MessageStatus.FAILED],
        MessageStatus.SENT: [MessageStatus.DELIVERED, MessageStatus.FAILED],
        MessageStatus.DELIVERED: [MessageStatus.READ],
        MessageStatus.READ: [],      # Terminal
        MessageStatus.FAILED: [],    # Terminal - NEVER transitions out
        MessageStatus.EXPIRED: [],   # Terminal
    }

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
        parent_message_id: Optional[UUID] = None,  # NEW: Link to parent for fallback tracking
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
        self.parent_message_id = parent_message_id  # NEW
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

        # Timestamps
        self.queued_at: Optional[datetime] = None
        self.sent_at: Optional[datetime] = None
        self.delivered_at: Optional[datetime] = None
        self.read_at: Optional[datetime] = None
        self.failed_at: Optional[datetime] = None

        # Delivery tracking
        self.aggregator: Optional[str] = None
        self.external_id: Optional[str] = None
        self.retry_count: int = 0
        self.max_retries: int = 3

        # 24h rolling window from creation
        self.expires_at: datetime = self.created_at + timedelta(hours=24)

        # Failure tracking
        self.failure_reason: Optional[FailureReason] = None
        self.error_code: Optional[str] = None
        self.error_message: Optional[str] = None

        # Metadata
        self.metadata: Dict[str, Any] = {}
        self.delivery_attempts: List[DeliveryAttempt] = []

        # Fallback enabled at campaign level (deprecated field - kept for compatibility)
        self.fallback_enabled: bool = True
        self.fallback_triggered: bool = False  # Deprecated - use parent_message_id != None

    @classmethod
    def create(
        cls,
        campaign_id: UUID,
        tenant_id: UUID,
        recipient_phone: str,
        content: MessageContent,
        priority: str = "medium",
        channel: MessageChannel = MessageChannel.RCS,
        parent_message_id: Optional[UUID] = None,  # NEW
    ) -> "Message":
        """Factory method to create a new message"""
        return cls(
            id=uuid4(),
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            recipient_phone=recipient_phone,
            content=content,
            priority=priority,
            channel=channel,
            parent_message_id=parent_message_id,
            status=MessageStatus.PENDING,
        )

    # State machine validation
    
    def _can_transition_to(self, new_status: MessageStatus) -> bool:
        """
        Check if status transition is valid.
        
        CRITICAL: Prevents FAILED → PENDING transitions
        """
        allowed_transitions = self._VALID_TRANSITIONS.get(self.status, [])
        return new_status in allowed_transitions

    def _transition_to(self, new_status: MessageStatus) -> None:
        """
        Transition to new status with validation.
        
        Raises ValueError if transition is invalid.
        """
        if not self._can_transition_to(new_status):
            raise ValueError(
                f"Invalid status transition: {self.status.value} → {new_status.value}. "
                f"Allowed transitions from {self.status.value}: "
                f"{[s.value for s in self._VALID_TRANSITIONS.get(self.status, [])]}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)

    # Status transition methods with validation
    
    def queue(self) -> None:
        """Mark message as queued for delivery"""
        self._transition_to(MessageStatus.QUEUED)
        self.queued_at = datetime.now(timezone.utc)

    def mark_sent(self, aggregator: str, external_id: str) -> None:
        """Mark message as sent via aggregator"""
        self._transition_to(MessageStatus.SENT)
        self.sent_at = datetime.now(timezone.utc)
        self.aggregator = aggregator
        self.external_id = external_id
        self._record_attempt(
            channel=self.channel,
            status=MessageStatus.SENT,
            aggregator=aggregator,
            external_id=external_id
        )

    def mark_delivered(self) -> None:
        """Mark message as delivered (DLR confirmation)"""
        self._transition_to(MessageStatus.DELIVERED)
        self.delivered_at = datetime.now(timezone.utc)

    def mark_read(self) -> None:
        """Mark message as read (RCS only)"""
        if self.channel != MessageChannel.RCS:
            return  # Silently ignore for non-RCS
        self._transition_to(MessageStatus.READ)
        self.read_at = datetime.now(timezone.utc)

    def mark_failed(
        self,
        reason: FailureReason,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Mark message as failed (TERMINAL STATE).
        
        Once failed, this message never transitions to any other status.
        Fallback creates a NEW message instead of modifying this one.
        """
        self._transition_to(MessageStatus.FAILED)
        self.failed_at = datetime.now(timezone.utc)
        self.failure_reason = reason
        self.error_code = error_code
        self.error_message = error_message
        self._record_attempt(
            channel=self.channel,
            status=MessageStatus.FAILED,
            aggregator=self.aggregator,
            error_code=error_code,
            error_message=error_message
        )

    def mark_expired(self) -> None:
        """Mark message as expired (can transition from any non-terminal status)"""
        if not self.is_terminal():
            self.status = MessageStatus.EXPIRED
            self.updated_at = datetime.now(timezone.utc)

    # Retry logic
    
    def should_retry(self) -> bool:
        """Check if message should be retried (failed but not terminal failure)"""
        if self.status != MessageStatus.FAILED:
            return False
        if self.retry_count >= self.max_retries:
            return False
        if self.is_expired():
            return False
        # Don't retry permanent failures
        if self.failure_reason in [
            FailureReason.INVALID_NUMBER,
            FailureReason.BLOCKED,
            FailureReason.RCS_NOT_SUPPORTED
        ]:
            return False
        return True

    def increment_retry(self) -> None:
        """Increment retry counter"""
        self.retry_count += 1
        self.updated_at = datetime.now(timezone.utc)

    # NEW: Parent-Child Fallback Pattern
    
    def should_trigger_fallback(self) -> bool:
        """
        Check if fallback should be triggered.
        
        NEW: This checks if THIS message qualifies for fallback creation.
        Does NOT modify this message - just returns boolean.
        
        Returns True if:
          - Status is FAILED
          - Channel is RCS (can't fallback from SMS)
          - Fallback enabled
          - Failure reason is RCS_NOT_SUPPORTED or exhausted retries
        """
        # Must be failed
        if self.status != MessageStatus.FAILED:
            return False
        
        # Must be RCS channel (can't fallback from SMS)
        if self.channel != MessageChannel.RCS:
            return False
        
        # Must have fallback enabled
        if not self.fallback_enabled:
            return False
        
        # Must be RCS-specific failure or exhausted retries
        if self.failure_reason == FailureReason.RCS_NOT_SUPPORTED:
            return True
        
        if self.retry_count >= self.max_retries:
            return True
        
        return False

    def create_fallback_message(self) -> "Message":
        """
        Create a NEW SMS fallback message linked to this one.
        
        NEW: Returns new Message instance - does NOT modify self.
        This is the key to fixing stuck messages!
        
        The new message:
          - Has channel=SMS
          - Has status=PENDING
          - Has parent_message_id=self.id
          - Uses SMS content (plain text from to_sms_text())
          - Shares campaign_id, tenant_id, recipient_phone
        
        Returns:
            New Message with channel=SMS, status=PENDING, parent_message_id=self.id
        
        Raises:
            ValueError: If fallback not allowed for this message
        """
        if not self.should_trigger_fallback():
            raise ValueError(
                f"Cannot create fallback message for {self.id}: "
                f"status={self.status}, channel={self.channel}, "
                f"failure_reason={self.failure_reason}"
            )
        
        # Create SMS content from original
        sms_content = MessageContent(
            text=self.content.to_sms_text(),
            template_id=None,  # SMS doesn't use RCS templates
            variables=[],
            rcs_type="BASIC"
        )
        
        # Create NEW message (not modifying self!)
        fallback_msg = Message.create(
            campaign_id=self.campaign_id,
            tenant_id=self.tenant_id,
            recipient_phone=self.recipient_phone,
            content=sms_content,
            priority=self.priority,
            channel=MessageChannel.SMS,  # Force SMS
            parent_message_id=self.id,  # Link to parent
        )
        
        # Copy metadata with fallback tracking
        fallback_msg.metadata = {
            **self.metadata,
            "fallback_from": str(self.id),
            "original_channel": "rcs",
            "fallback_reason": self.failure_reason.value if self.failure_reason else "unknown",
            "sms_text": sms_content.text
        }
        
        # Mark this flag for compatibility (deprecated)
        fallback_msg.fallback_triggered = True
        
        return fallback_msg

    # DEPRECATED: Old fallback methods (kept for compatibility, will be removed)
    
    def should_fallback_to_sms(self) -> bool:
        """Deprecated: Use should_trigger_fallback() instead"""
        return self.should_trigger_fallback()
    
    def trigger_fallback(self) -> None:
        """
        Deprecated: DO NOT USE - causes FAILED → PENDING transition!
        Use create_fallback_message() instead.
        """
        raise NotImplementedError(
            "trigger_fallback() is deprecated and dangerous (causes FAILED → PENDING). "
            "Use create_fallback_message() instead."
        )

    # Helper methods
    
    def is_delivered(self) -> bool:
        """Check if message was successfully delivered"""
        return self.status in [MessageStatus.DELIVERED, MessageStatus.READ]

    def is_terminal(self) -> bool:
        """Check if message is in terminal state"""
        return self.status in [MessageStatus.FAILED, MessageStatus.EXPIRED, MessageStatus.READ]

    def is_expired(self) -> bool:
        """Check if message has expired"""
        return datetime.now(timezone.utc) > self.expires_at

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to E.164 format"""
        digits = ''.join(filter(str.isdigit, phone))
        if not phone.startswith('+'):
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
        error_message: Optional[str] = None
    ) -> None:
        """Record delivery attempt"""
        self.delivery_attempts.append(DeliveryAttempt(
            attempt_number=len(self.delivery_attempts) + 1,
            channel=channel,
            attempted_at=datetime.now(timezone.utc),
            status=status,
            aggregator=aggregator,
            error_code=error_code,
            error_message=error_message,
            external_id=external_id,
        ))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "campaign_id": str(self.campaign_id),
            "tenant_id": str(self.tenant_id),
            "recipient_phone": self.recipient_phone,
            "status": self.status.value,
            "channel": self.channel.value,
            "priority": self.priority,
            "parent_message_id": str(self.parent_message_id) if self.parent_message_id else None,
            "template_id": self.content.template_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "retry_count": self.retry_count,
            "fallback_triggered": self.fallback_triggered,
            "aggregator": self.aggregator,
            "external_id": self.external_id,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
        }
