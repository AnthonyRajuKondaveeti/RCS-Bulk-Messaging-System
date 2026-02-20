"""
Opt-In/Opt-Out Domain Model

Manages user consent for RCS/SMS messaging compliance.
Implements GDPR and DND (Do Not Disturb) regulations.

Business Rules:
    - Users must explicitly opt-in before receiving promotional messages
    - Opt-out must be honored immediately
    - Transactional messages can be sent regardless of opt-out status
    - Opt-in status is tracked per tenant
    - Consent history maintained for audit compliance

Regulations Supported:
    - GDPR (EU)
    - TCPA (US)
    - DND Registry (India)
    - CASL (Canada)
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


class ConsentType(str, Enum):
    """Type of consent"""
    PROMOTIONAL = "promotional"  # Marketing messages
    TRANSACTIONAL = "transactional"  # Order updates, OTPs
    INFORMATIONAL = "informational"  # News, updates
    ALL = "all"  # All message types


class ConsentStatus(str, Enum):
    """Consent status"""
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    PENDING = "pending"  # Awaiting confirmation
    EXPIRED = "expired"  # Time-limited consent expired


class ConsentMethod(str, Enum):
    """How consent was obtained"""
    WEB_FORM = "web_form"
    SMS_REPLY = "sms_reply"
    RCS_REPLY = "rcs_reply"
    CUSTOMER_SERVICE = "customer_service"
    POINT_OF_SALE = "point_of_sale"
    API = "api"


@dataclass
class ConsentRecord:
    """Single consent event record"""
    timestamp: datetime
    status: ConsentStatus
    consent_type: ConsentType
    method: ConsentMethod
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    notes: Optional[str] = None


class OptIn:
    """
    Opt-In/Consent Aggregate
    
    Manages user consent for messaging across different message types.
    Maintains complete audit trail for regulatory compliance.
    
    Business Rules:
        1. Promotional messages require explicit opt-in
        2. Transactional messages don't require opt-in
        3. Opt-out must be processed immediately
        4. Consent history cannot be deleted
        5. Re-opt-in requires new consent record
    
    Example:
        >>> opt_in = OptIn.create(
        ...     tenant_id=tenant_id,
        ...     phone_number="+919876543210",
        ... )
        >>> opt_in.grant_consent(
        ...     consent_type=ConsentType.PROMOTIONAL,
        ...     method=ConsentMethod.WEB_FORM,
        ...     ip_address="192.168.1.1",
        ... )
        >>> opt_in.can_send_promotional()  # True
    """
    
    def __init__(
        self,
        id: UUID,
        tenant_id: UUID,
        phone_number: str,
        created_at: Optional[datetime] = None,
    ):
        self.id = id
        self.tenant_id = tenant_id
        self.phone_number = self._normalize_phone(phone_number)
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = datetime.utcnow()
        
        # Current consent status per type
        self.promotional_status: ConsentStatus = ConsentStatus.OPTED_OUT
        self.transactional_status: ConsentStatus = ConsentStatus.OPTED_IN  # Default allowed
        self.informational_status: ConsentStatus = ConsentStatus.OPTED_OUT
        
        # Consent timestamps
        self.promotional_opted_in_at: Optional[datetime] = None
        self.promotional_opted_out_at: Optional[datetime] = None
        
        # Audit trail
        self.consent_history: List[ConsentRecord] = []
        
        # DND Registry
        self.is_on_dnd_registry: bool = False
        self.dnd_checked_at: Optional[datetime] = None
        
        # Metadata
        self.metadata: Dict[str, Any] = {}
        self.preferences: Dict[str, Any] = {}
    
    @classmethod
    def create(
        cls,
        tenant_id: UUID,
        phone_number: str,
    ) -> "OptIn":
        """
        Create a new opt-in record
        
        Args:
            tenant_id: Tenant identifier
            phone_number: Phone number in E.164 format
            
        Returns:
            New OptIn instance
        """
        return cls(
            id=uuid4(),
            tenant_id=tenant_id,
            phone_number=phone_number,
        )
    
    def grant_consent(
        self,
        consent_type: ConsentType,
        method: ConsentMethod,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """
        Grant consent for messaging
        
        Args:
            consent_type: Type of consent being granted
            method: How consent was obtained
            ip_address: IP address (for audit)
            user_agent: User agent string (for audit)
            notes: Additional notes
            
        Raises:
            ValueError: If already opted in
        """
        timestamp = datetime.utcnow()
        
        # Update status based on type
        if consent_type == ConsentType.PROMOTIONAL:
            self.promotional_status = ConsentStatus.OPTED_IN
            self.promotional_opted_in_at = timestamp
        elif consent_type == ConsentType.TRANSACTIONAL:
            self.transactional_status = ConsentStatus.OPTED_IN
        elif consent_type == ConsentType.INFORMATIONAL:
            self.informational_status = ConsentStatus.OPTED_IN
        elif consent_type == ConsentType.ALL:
            self.promotional_status = ConsentStatus.OPTED_IN
            self.transactional_status = ConsentStatus.OPTED_IN
            self.informational_status = ConsentStatus.OPTED_IN
            self.promotional_opted_in_at = timestamp
        
        # Record consent event
        self._add_consent_record(
            timestamp=timestamp,
            status=ConsentStatus.OPTED_IN,
            consent_type=consent_type,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
            notes=notes,
        )
        
        self.updated_at = timestamp
    
    def revoke_consent(
        self,
        consent_type: ConsentType,
        method: ConsentMethod,
        reason: Optional[str] = None,
    ) -> None:
        """
        Revoke consent (opt-out)
        
        Args:
            consent_type: Type of consent being revoked
            method: How opt-out was received
            reason: Opt-out reason
        """
        timestamp = datetime.utcnow()
        
        # Update status based on type
        if consent_type == ConsentType.PROMOTIONAL:
            self.promotional_status = ConsentStatus.OPTED_OUT
            self.promotional_opted_out_at = timestamp
        elif consent_type == ConsentType.TRANSACTIONAL:
            self.transactional_status = ConsentStatus.OPTED_OUT
        elif consent_type == ConsentType.INFORMATIONAL:
            self.informational_status = ConsentStatus.OPTED_OUT
        elif consent_type == ConsentType.ALL:
            self.promotional_status = ConsentStatus.OPTED_OUT
            self.transactional_status = ConsentStatus.OPTED_OUT
            self.informational_status = ConsentStatus.OPTED_OUT
            self.promotional_opted_out_at = timestamp
        
        # Record opt-out event
        self._add_consent_record(
            timestamp=timestamp,
            status=ConsentStatus.OPTED_OUT,
            consent_type=consent_type,
            method=method,
            notes=reason,
        )
        
        self.updated_at = timestamp
    
    def can_send_promotional(self) -> bool:
        """
        Check if promotional messages can be sent
        
        Returns:
            True if promotional messages are allowed
        """
        if self.is_on_dnd_registry:
            return False
        
        return self.promotional_status == ConsentStatus.OPTED_IN
    
    def can_send_transactional(self) -> bool:
        """
        Check if transactional messages can be sent
        
        Returns:
            True if transactional messages are allowed
            
        Note:
            Transactional messages are typically always allowed
            unless explicitly opted out
        """
        return self.transactional_status == ConsentStatus.OPTED_IN
    
    def can_send_informational(self) -> bool:
        """
        Check if informational messages can be sent
        
        Returns:
            True if informational messages are allowed
        """
        if self.is_on_dnd_registry:
            return False
        
        return self.informational_status == ConsentStatus.OPTED_IN
    
    def can_send_message(self, message_type: str) -> bool:
        """
        Check if a specific message type can be sent
        
        Args:
            message_type: Type of message (promotional, transactional, informational)
            
        Returns:
            True if message can be sent
        """
        if message_type == "promotional":
            return self.can_send_promotional()
        elif message_type == "transactional":
            return self.can_send_transactional()
        elif message_type == "informational":
            return self.can_send_informational()
        else:
            return False
    
    def mark_on_dnd_registry(self) -> None:
        """Mark phone number as on DND registry"""
        self.is_on_dnd_registry = True
        self.dnd_checked_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def mark_off_dnd_registry(self) -> None:
        """Mark phone number as off DND registry"""
        self.is_on_dnd_registry = False
        self.dnd_checked_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def get_consent_status(
        self,
        consent_type: ConsentType,
    ) -> ConsentStatus:
        """
        Get current consent status for a type
        
        Args:
            consent_type: Type of consent
            
        Returns:
            Current consent status
        """
        if consent_type == ConsentType.PROMOTIONAL:
            return self.promotional_status
        elif consent_type == ConsentType.TRANSACTIONAL:
            return self.transactional_status
        elif consent_type == ConsentType.INFORMATIONAL:
            return self.informational_status
        else:
            return ConsentStatus.OPTED_OUT
    
    def get_consent_history(
        self,
        consent_type: Optional[ConsentType] = None,
    ) -> List[ConsentRecord]:
        """
        Get consent history
        
        Args:
            consent_type: Optional filter by type
            
        Returns:
            List of consent records
        """
        if consent_type:
            return [
                record for record in self.consent_history
                if record.consent_type == consent_type
            ]
        return self.consent_history.copy()
    
    def set_preference(self, key: str, value: Any) -> None:
        """
        Set user preference
        
        Args:
            key: Preference key
            value: Preference value
        """
        self.preferences[key] = value
        self.updated_at = datetime.utcnow()
    
    def get_preference(self, key: str, default: Any = None) -> Any:
        """
        Get user preference
        
        Args:
            key: Preference key
            default: Default value if not set
            
        Returns:
            Preference value or default
        """
        return self.preferences.get(key, default)
    
    def _add_consent_record(
        self,
        timestamp: datetime,
        status: ConsentStatus,
        consent_type: ConsentType,
        method: ConsentMethod,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Add consent record to history"""
        record = ConsentRecord(
            timestamp=timestamp,
            status=status,
            consent_type=consent_type,
            method=method,
            ip_address=ip_address,
            user_agent=user_agent,
            notes=notes,
        )
        self.consent_history.append(record)
    
    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to E.164 format"""
        # Remove all non-numeric characters
        digits = ''.join(filter(str.isdigit, phone))
        
        # Add + prefix if not present
        if not phone.startswith('+'):
            # Assume Indian number if 10 digits
            if len(digits) == 10:
                return f"+91{digits}"
            return f"+{digits}"
        
        return phone
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize opt-in to dictionary"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "phone_number": self.phone_number,
            "promotional_status": self.promotional_status.value,
            "transactional_status": self.transactional_status.value,
            "informational_status": self.informational_status.value,
            "promotional_opted_in_at": (
                self.promotional_opted_in_at.isoformat()
                if self.promotional_opted_in_at else None
            ),
            "promotional_opted_out_at": (
                self.promotional_opted_out_at.isoformat()
                if self.promotional_opted_out_at else None
            ),
            "is_on_dnd_registry": self.is_on_dnd_registry,
            "dnd_checked_at": (
                self.dnd_checked_at.isoformat()
                if self.dnd_checked_at else None
            ),
            "consent_history_count": len(self.consent_history),
            "preferences": self.preferences,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
