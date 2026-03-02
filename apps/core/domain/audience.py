"""
Audience Domain Model

Represents a collection of contacts/recipients for campaigns.

Features:
    - Contact lists with metadata
    - CSV import support
    - Segmentation filters
    - Dynamic audiences (query-based)
    - Static audiences (uploaded lists)
    
Audience Types:
    - STATIC: Fixed list of contacts (CSV upload)
    - DYNAMIC: Query-based segmentation
    - SUPPRESSION: Exclude list (DND, opt-outs)
"""

from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field


class AudienceType(Enum):
    """Type of audience"""
    STATIC = "static"  # Fixed list
    DYNAMIC = "dynamic"  # Query-based
    SUPPRESSION = "suppression"  # Exclusion list


class AudienceStatus(Enum):
    """Audience status"""
    DRAFT = "draft"
    PROCESSING = "processing"  # CSV being processed
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass
class Contact:
    """
    Individual contact in an audience
    
    Attributes:
        phone_number: E.164 format (+919876543210)
        metadata: Custom fields (name, email, order_id, etc.)
    """
    phone_number: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate phone number format"""
        if not self.phone_number.startswith('+'):
            raise ValueError(f"Phone number must be in E.164 format: {self.phone_number}")


class Audience:
    """
    Audience aggregate root
    
    Represents a collection of contacts for targeting campaigns.
    
    Example:
        # Static audience from CSV
        audience = Audience.create(
            tenant_id=tenant_id,
            name="Black Friday Customers",
            audience_type=AudienceType.STATIC,
        )
        audience.add_contacts([
            Contact("+919876543210", {"name": "John"}),
            Contact("+919876543211", {"name": "Jane"}),
        ])
        
        # Dynamic audience from query
        audience = Audience.create_dynamic(
            tenant_id=tenant_id,
            name="High Value Customers",
            query={
                "segment": "premium",
                "min_orders": 5,
                "min_value": 10000,
            }
        )
    """
    
    def __init__(
        self,
        id: UUID,
        tenant_id: UUID,
        name: str,
        audience_type: AudienceType,
        created_at: datetime,
        updated_at: datetime,
    ):
        self.id = id
        self.tenant_id = tenant_id
        self.name = name
        self.audience_type = audience_type
        self.status = AudienceStatus.DRAFT
        
        # Metadata
        self.description: Optional[str] = None
        self.tags: List[str] = []
        
        # Contacts (for static audiences)
        self.contacts: List[Contact] = []
        
        # Query (for dynamic audiences)
        self.query: Optional[Dict[str, Any]] = None
        
        # Statistics
        self.total_contacts = 0
        self.valid_contacts = 0
        self.invalid_contacts = 0
        
        # Timestamps
        self.created_at = created_at
        self.updated_at = updated_at
        self.last_used_at: Optional[datetime] = None
    
    @classmethod
    def create(
        cls,
        tenant_id: UUID,
        name: str,
        audience_type: AudienceType = AudienceType.STATIC,
    ) -> "Audience":
        """Create new audience"""
        now = datetime.utcnow()
        
        audience = cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            audience_type=audience_type,
            created_at=now,
            updated_at=now,
        )
        
        return audience
    
    @classmethod
    def create_dynamic(
        cls,
        tenant_id: UUID,
        name: str,
        query: Dict[str, Any],
    ) -> "Audience":
        """Create dynamic audience with query"""
        audience = cls.create(
            tenant_id=tenant_id,
            name=name,
            audience_type=AudienceType.DYNAMIC,
        )
        
        audience.query = query
        
        return audience
    
    def add_contacts(self, contacts: List[Contact]) -> None:
        """
        Add contacts to static audience
        
        Args:
            contacts: List of Contact objects
            
        Raises:
            ValueError: If audience is not static
        """
        if self.audience_type != AudienceType.STATIC:
            raise ValueError("Can only add contacts to static audiences")
        
        # Validate and deduplicate
        phone_set = {c.phone_number for c in self.contacts}
        
        for contact in contacts:
            if contact.phone_number not in phone_set:
                self.contacts.append(contact)
                phone_set.add(contact.phone_number)
                self.valid_contacts += 1
        
        self.total_contacts = len(self.contacts)
        self.updated_at = datetime.utcnow()
    
    def remove_contact(self, phone_number: str) -> bool:
        """
        Remove contact from audience
        
        Returns:
            True if contact was removed, False if not found
        """
        initial_count = len(self.contacts)
        self.contacts = [c for c in self.contacts if c.phone_number != phone_number]
        
        if len(self.contacts) < initial_count:
            self.total_contacts = len(self.contacts)
            self.valid_contacts = len(self.contacts)
            self.updated_at = datetime.utcnow()
            return True
        
        return False
    
    def import_from_csv(self, rows: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Import contacts from CSV data
        
        Args:
            rows: List of dicts with 'phone' key and optional metadata
            
        Returns:
            Dictionary with import statistics
        """
        if self.audience_type != AudienceType.STATIC:
            raise ValueError("Can only import CSV to static audiences")
        
        self.status = AudienceStatus.PROCESSING
        
        imported = 0
        skipped = 0
        errors = []
        
        for row in rows:
            try:
                # Get phone number
                phone = row.get('phone') or row.get('phone_number')
                if not phone:
                    skipped += 1
                    continue
                
                # Normalize phone number
                phone = self._normalize_phone(phone)
                
                # Extract metadata (all columns except phone)
                metadata = {k: v for k, v in row.items() 
                           if k not in ['phone', 'phone_number']}
                
                # Create contact
                contact = Contact(phone_number=phone, metadata=metadata)
                self.contacts.append(contact)
                imported += 1
                
            except Exception as e:
                errors.append(str(e))
                skipped += 1
        
        self.total_contacts = len(self.contacts)
        self.valid_contacts = imported
        self.invalid_contacts = skipped
        self.status = AudienceStatus.ACTIVE
        self.updated_at = datetime.utcnow()
        
        return {
            "imported": imported,
            "skipped": skipped,
            "total_contacts": self.total_contacts,
            "errors": errors[:10],  # First 10 errors
        }
    
    def get_contacts(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Contact]:
        """
        Get contacts with pagination
        
        For dynamic audiences, this would execute the query.
        For static audiences, returns stored contacts.
        """
        if self.audience_type == AudienceType.DYNAMIC:
            # TODO: Execute query against customer database
            # For now, return empty list
            return []
        
        # Static audience
        contacts = self.contacts[offset:]
        if limit:
            contacts = contacts[:limit]
        
        return contacts
    
    def get_phone_numbers(self) -> List[str]:
        """Get list of all phone numbers"""
        return [c.phone_number for c in self.contacts]
    
    def activate(self) -> None:
        """Activate audience for use in campaigns"""
        if self.status != AudienceStatus.DRAFT:
            raise ValueError(f"Cannot activate audience in {self.status} status")
        
        if self.total_contacts == 0:
            raise ValueError("Cannot activate empty audience")
        
        self.status = AudienceStatus.ACTIVE
        self.updated_at = datetime.utcnow()
    
    def archive(self) -> None:
        """Archive audience (can't be used in campaigns)"""
        self.status = AudienceStatus.ARCHIVED
        self.updated_at = datetime.utcnow()
    
    def mark_used(self) -> None:
        """Mark audience as used in a campaign"""
        self.last_used_at = datetime.utcnow()
    
    def _normalize_phone(self, phone: str) -> str:
        """
        Normalize phone number to E.164 format
        
        Simple implementation - in production, use phonenumbers library
        """
        # Remove spaces, dashes, parentheses
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        
        # Add + if not present
        if not phone.startswith('+'):
            # Assume Indian number if no country code
            if len(phone) == 10:
                phone = '+91' + phone
            else:
                phone = '+' + phone
        
        return phone
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "audience_type": self.audience_type.value,
            "status": self.status.value,
            "description": self.description,
            "tags": self.tags,
            "total_contacts": self.total_contacts,
            "valid_contacts": self.valid_contacts,
            "invalid_contacts": self.invalid_contacts,
            "query": self.query,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }
