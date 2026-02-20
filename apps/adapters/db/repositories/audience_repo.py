"""
Audience Repository

Database operations for audiences and contacts.

Features:
    - CRUD operations for audiences
    - Contact list management
    - Efficient querying and pagination
    - Contact deduplication
"""

from typing import List, Optional
from uuid import UUID
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.audience import Audience, AudienceType, AudienceStatus, Contact
from apps.adapters.db.models import AudienceModel


class AudienceRepository:
    """
    Repository for audience persistence
    
    Handles conversion between domain models and database models.
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def save(self, audience: Audience) -> None:
        """
        Save audience to database
        
        Creates new record or updates existing one.
        """
        # Check if exists
        stmt = select(AudienceModel).where(AudienceModel.id == audience.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        # Serialize contacts
        contacts_data = [
            {
                "phone_number": c.phone_number,
                "metadata": c.metadata,
            }
            for c in audience.contacts
        ]
        
        if existing:
            # Update existing
            existing.name = audience.name
            existing.status = audience.status.value
            existing.description = audience.description
            existing.tags = audience.tags
            existing.query = audience.query
            existing.contacts = contacts_data
            existing.total_contacts = audience.total_contacts
            existing.valid_contacts = audience.valid_contacts
            existing.invalid_contacts = audience.invalid_contacts
            existing.last_used_at = audience.last_used_at
            existing.updated_at = audience.updated_at
        else:
            # Create new
            model = AudienceModel(
                id=audience.id,
                tenant_id=audience.tenant_id,
                name=audience.name,
                audience_type=audience.audience_type.value,
                status=audience.status.value,
                description=audience.description,
                tags=audience.tags,
                query=audience.query,
                contacts=contacts_data,
                total_contacts=audience.total_contacts,
                valid_contacts=audience.valid_contacts,
                invalid_contacts=audience.invalid_contacts,
                created_at=audience.created_at,
                updated_at=audience.updated_at,
                last_used_at=audience.last_used_at,
            )
            self.session.add(model)
    
    async def get_by_id(
        self,
        audience_id: UUID,
        tenant_id: Optional[UUID] = None,
    ) -> Optional[Audience]:
        """
        Get audience by ID
        
        Args:
            audience_id: Audience UUID
            tenant_id: Optional tenant ID for multi-tenant filtering
            
        Returns:
            Audience domain model or None if not found
        """
        stmt = select(AudienceModel).where(AudienceModel.id == audience_id)
        
        if tenant_id:
            stmt = stmt.where(AudienceModel.tenant_id == tenant_id)
        
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return self._to_domain(model)
    
    async def get_by_tenant(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[AudienceStatus] = None,
        audience_type: Optional[AudienceType] = None,
    ) -> List[Audience]:
        """
        Get audiences for a tenant
        
        Args:
            tenant_id: Tenant UUID
            limit: Max results
            offset: Pagination offset
            status: Optional status filter
            audience_type: Optional type filter
            
        Returns:
            List of Audience domain models
        """
        stmt = select(AudienceModel).where(AudienceModel.tenant_id == tenant_id)
        
        if status:
            stmt = stmt.where(AudienceModel.status == status.value)
        if audience_type:
            stmt = stmt.where(AudienceModel.audience_type == audience_type.value)
        
        stmt = stmt.order_by(AudienceModel.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]
    
    async def count_by_tenant(
        self,
        tenant_id: UUID,
        status: Optional[AudienceStatus] = None,
        audience_type: Optional[AudienceType] = None,
    ) -> int:
        """
        Count audiences for a tenant
        
        Args:
            tenant_id: Tenant UUID
            status: Optional status filter
            audience_type: Optional type filter
            
        Returns:
            Total count
        """
        stmt = select(func.count()).select_from(AudienceModel)
        stmt = stmt.where(AudienceModel.tenant_id == tenant_id)
        
        if status:
            stmt = stmt.where(AudienceModel.status == status.value)
        if audience_type:
            stmt = stmt.where(AudienceModel.audience_type == audience_type.value)
        
        result = await self.session.execute(stmt)
        return result.scalar()
    
    async def delete(self, audience_id: UUID) -> None:
        """
        Delete audience
        
        Args:
            audience_id: Audience UUID
        """
        stmt = select(AudienceModel).where(AudienceModel.id == audience_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if model:
            await self.session.delete(model)
    
    async def get_active_audiences(
        self,
        tenant_id: UUID,
    ) -> List[Audience]:
        """
        Get all active audiences for a tenant
        
        Useful for campaign creation.
        """
        return await self.get_by_tenant(
            tenant_id=tenant_id,
            status=AudienceStatus.ACTIVE,
            limit=1000,
        )
    
    async def get_phone_numbers(
        self,
        audience_id: UUID,
    ) -> List[str]:
        """
        Get all phone numbers from an audience
        
        Optimized for campaign execution.
        
        Args:
            audience_id: Audience UUID
            
        Returns:
            List of phone numbers in E.164 format
        """
        stmt = select(AudienceModel.contacts).where(
            AudienceModel.id == audience_id
        )
        result = await self.session.execute(stmt)
        contacts_data = result.scalar_one_or_none()
        
        if not contacts_data:
            return []
        
        return [c["phone_number"] for c in contacts_data]
    
    def _to_domain(self, model: AudienceModel) -> Audience:
        """
        Convert database model to domain model
        
        Args:
            model: AudienceModel from database
            
        Returns:
            Audience domain model
        """
        audience = Audience(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            audience_type=AudienceType(model.audience_type),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        
        # Set status and metadata
        audience.status = AudienceStatus(model.status)
        audience.description = model.description
        audience.tags = model.tags or []
        audience.query = model.query
        
        # Set statistics
        audience.total_contacts = model.total_contacts
        audience.valid_contacts = model.valid_contacts
        audience.invalid_contacts = model.invalid_contacts
        audience.last_used_at = model.last_used_at
        
        # Load contacts
        if model.contacts:
            for contact_data in model.contacts:
                contact = Contact(
                    phone_number=contact_data["phone_number"],
                    metadata=contact_data.get("metadata", {}),
                )
                audience.contacts.append(contact)
        
        return audience
