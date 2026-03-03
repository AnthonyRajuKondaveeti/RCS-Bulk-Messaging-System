"""
Message Domain Model

FIXES applied:
  1. MessageContent gains template_id + variables fields (required by rcssms.in)
  2. expires_at = created_at + 24h (not end-of-day – old code broke for late-night messages)
  3. Removed duplicate mark_fallback_sent / to_sms_text methods
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class MessageStatus(str, Enum):
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
    Message Aggregate — tracks full delivery lifecycle.

    Business Rules:
        1. RCS attempted before SMS fallback
        2. Max 3 retries per channel
        3. Expires 24 hours after creation (not end-of-day)
        4. Read receipts only for RCS
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

        self.queued_at: Optional[datetime] = None
        self.sent_at: Optional[datetime] = None
        self.delivered_at: Optional[datetime] = None
        self.read_at: Optional[datetime] = None
        self.failed_at: Optional[datetime] = None

        # FIX: 24h rolling window, not .replace(hour=23, minute=59)
        self.expires_at: datetime = self.created_at + timedelta(hours=24)

        self.retry_count: int = 0
        self.max_retries: int = 3
        self.fallback_enabled: bool = True
        self.fallback_triggered: bool = False

        self.delivery_attempts: List[DeliveryAttempt] = []
        self.aggregator: Optional[str] = None
        self.external_id: Optional[str] = None
        self.failure_reason: Optional[FailureReason] = None
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
        if self.status != MessageStatus.PENDING:
            raise ValueError(f"Cannot queue message in {self.status} status")
        self.status = MessageStatus.QUEUED
        self.queued_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def mark_sent(self, aggregator: str, external_id: str) -> None:
        self.status = MessageStatus.SENT
        self.sent_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.aggregator = aggregator
        self.external_id = external_id
        self._record_attempt(channel=self.channel, status=MessageStatus.SENT,
                             aggregator=aggregator, external_id=external_id)

    def mark_delivered(self) -> None:
        if self.status not in [MessageStatus.SENT, MessageStatus.QUEUED]:
            raise ValueError(f"Cannot mark delivered from {self.status} status")
        self.status = MessageStatus.DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self._record_attempt(channel=self.channel, status=MessageStatus.DELIVERED,
                             aggregator=self.aggregator)

    def mark_read(self) -> None:
        if self.channel != MessageChannel.RCS:
            return
        if self.status not in [MessageStatus.DELIVERED, MessageStatus.SENT]:
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
        self.status = MessageStatus.FAILED
        self.failed_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.failure_reason = reason
        self._record_attempt(channel=self.channel, status=MessageStatus.FAILED,
                             aggregator=self.aggregator, error_code=error_code,
                             error_message=error_message)

    def should_retry(self) -> bool:
        if self.status != MessageStatus.FAILED:
            return False
        if self.retry_count >= self.max_retries:
            return False
        if datetime.now(timezone.utc) > self.expires_at:
            return False
        if self.failure_reason in [FailureReason.INVALID_NUMBER, FailureReason.BLOCKED]:
            return False
        return True

    def should_fallback_to_sms(self) -> bool:
        if not self.fallback_enabled:
            return False
        if self.fallback_triggered:
            return False
        if self.channel == MessageChannel.SMS:
            return False
        if self.failure_reason == FailureReason.RCS_NOT_SUPPORTED:
            return True
        if self.retry_count >= self.max_retries:
            return True
        return False

    def trigger_fallback(self) -> None:
        if not self.should_fallback_to_sms():
            raise ValueError("Cannot trigger fallback for this message")
        self.channel = MessageChannel.SMS
        self.status = MessageStatus.PENDING
        self.fallback_triggered = True
        self.retry_count = 0
        self.updated_at = datetime.now(timezone.utc)
        self.metadata["original_channel"] = "rcs"
        self.metadata["sms_text"] = self.content.to_sms_text()

    def mark_fallback_sent(self, aggregator: str, external_id: str) -> None:
        self.status = MessageStatus.FALLBACK_SENT
        self.sent_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        self.aggregator = aggregator
        self.external_id = external_id
        self._record_attempt(channel=MessageChannel.SMS,
                             status=MessageStatus.FALLBACK_SENT,
                             aggregator=aggregator, external_id=external_id)

    def mark_fallback_delivered(self) -> None:
        self.status = MessageStatus.FALLBACK_DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def increment_retry(self) -> None:
        self.retry_count += 1
        self.updated_at = datetime.now(timezone.utc)

    def is_delivered(self) -> bool:
        return self.status in [MessageStatus.DELIVERED, MessageStatus.READ,
                                MessageStatus.FALLBACK_DELIVERED]

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def _normalize_phone(self, phone: str) -> str:
        digits = ''.join(filter(str.isdigit, phone))
        if not phone.startswith('+'):
            if len(digits) == 10:
                return f"+91{digits}"
            return f"+{digits}"
        return phone

    def _record_attempt(self, channel, status, aggregator=None,
                        external_id=None, error_code=None, error_message=None):
        self.delivery_attempts.append(DeliveryAttempt(
            attempt_number=len(self.delivery_attempts) + 1,
            channel=channel, attempted_at=datetime.now(timezone.utc),
            status=status, aggregator=aggregator, error_code=error_code,
            error_message=error_message, external_id=external_id,
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
            "template_id": self.content.template_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "retry_count": self.retry_count,
            "fallback_triggered": self.fallback_triggered,
            "aggregator": self.aggregator,
            "external_id": self.external_id,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
        }
