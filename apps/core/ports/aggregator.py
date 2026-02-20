"""
Aggregator Port Interface

Defines the contract for RCS/SMS aggregator implementations.
This abstraction allows switching between vendors (Gupshup, Route Mobile, Infobip)
without changing business logic.

Implementations:
    - GupshupAdapter
    - RouteAdapter
    - InfobipAdapter
    
Design Pattern: Hexagonal Architecture (Ports & Adapters)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime

from apps.core.domain.message import Message, MessageChannel, RichCard, SuggestedAction


@dataclass
class SendMessageRequest:
    """Request to send a message via aggregator"""
    message_id: UUID
    recipient_phone: str
    channel: MessageChannel
    content_text: str
    rich_card: Optional[RichCard] = None
    suggestions: List[SuggestedAction] = None
    priority: str = "medium"
    callback_url: Optional[str] = None
    metadata: Dict[str, Any] = None


@dataclass
class SendMessageResponse:
    """Response from aggregator after sending message"""
    success: bool
    external_id: Optional[str] = None  # Vendor's message ID
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_after: Optional[int] = None  # Seconds to wait before retry


@dataclass
class DeliveryStatus:
    """Delivery status update from aggregator"""
    message_id: UUID
    external_id: str
    status: str  # "sent", "delivered", "read", "failed"
    timestamp: datetime
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None


@dataclass
class CapabilityCheckResult:
    """Result of RCS capability check"""
    phone_number: str
    rcs_enabled: bool
    last_checked: datetime
    features: List[str] = None  # ["rich_cards", "suggestions", "receipts"]


class AggregatorPort(ABC):
    """
    Abstract interface for RCS/SMS aggregators
    
    All aggregator adapters must implement this interface to ensure
    compatibility with the platform's business logic.
    
    Thread Safety: Implementations must be thread-safe
    Idempotency: send_message should be idempotent based on message_id
    """
    
    @abstractmethod
    async def send_rcs_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send an RCS message with rich content
        
        Args:
            request: Message send request with rich content
            
        Returns:
            Response with external ID or error details
            
        Raises:
            AggregatorException: On communication failure
            ValidationException: On invalid request data
        """
        pass
    
    @abstractmethod
    async def send_sms_message(
        self,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        Send a plain SMS message (fallback)
        
        Args:
            request: Message send request (text only)
            
        Returns:
            Response with external ID or error details
            
        Raises:
            AggregatorException: On communication failure
        """
        pass
    
    @abstractmethod
    async def check_rcs_capability(
        self,
        phone_numbers: List[str],
    ) -> List[CapabilityCheckResult]:
        """
        Check RCS capability for phone numbers
        
        Args:
            phone_numbers: List of phone numbers in E.164 format
            
        Returns:
            Capability status for each phone number
            
        Note:
            Results should be cached for performance
        """
        pass
    
    @abstractmethod
    async def get_delivery_status(
        self,
        external_id: str,
    ) -> Optional[DeliveryStatus]:
        """
        Query delivery status from aggregator
        
        Args:
            external_id: Vendor's message ID
            
        Returns:
            Current delivery status or None if not found
        """
        pass
    
    @abstractmethod
    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Optional[DeliveryStatus]:
        """
        Parse webhook callback from aggregator
        
        Args:
            payload: Webhook request body
            headers: HTTP headers (for signature verification)
            
        Returns:
            Parsed delivery status or None if invalid
            
        Raises:
            WebhookValidationException: If signature is invalid
        """
        pass
    
    @abstractmethod
    def validate_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Validate webhook signature for security
        
        Args:
            payload: Raw request body
            signature: Signature from header
            
        Returns:
            True if signature is valid
        """
        pass
    
    @abstractmethod
    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Get current account balance/credits
        
        Returns:
            Dictionary with balance info
            Example: {"balance": 1000.0, "currency": "INR", "credits": 50000}
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """
        Get aggregator name
        
        Returns:
            Aggregator identifier (e.g., "gupshup", "route")
        """
        pass


class AggregatorException(Exception):
    """Base exception for aggregator errors"""
    
    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        retry_after: Optional[int] = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.retry_after = retry_after


class ValidationException(Exception):
    """Raised when request validation fails"""
    pass


class WebhookValidationException(Exception):
    """Raised when webhook signature validation fails"""
    pass


class RateLimitException(AggregatorException):
    """Raised when rate limit is exceeded"""
    pass


class InsufficientBalanceException(AggregatorException):
    """Raised when account has insufficient balance"""
    pass
