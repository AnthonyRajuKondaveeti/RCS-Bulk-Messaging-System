"""
Audience API Routes

Manage contact lists and audiences for campaigns.

Features:
    - Create/Read/Update/Delete audiences
    - Upload CSV files with contacts
    - Add individual contacts
    - List audiences with filtering
    - Get audience statistics
    
Endpoints:
    POST   /audiences              - Create audience
    GET    /audiences              - List audiences
    GET    /audiences/{id}         - Get audience
    DELETE /audiences/{id}          - Delete audience
    POST   /audiences/{id}/upload  - Upload CSV
    POST   /audiences/{id}/contacts - Add contacts manually
    GET    /audiences/{id}/contacts - Get contacts (paginated)
    GET    /audiences/{id}/stats    - Get statistics
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import csv
import io

from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.adapters.db.postgres import get_db_session
from apps.core.domain.audience import Audience, AudienceType, AudienceStatus, Contact
from apps.api.middleware.auth import get_current_tenant


router = APIRouter(prefix="/audiences", tags=["Audiences"])


# Request/Response Models

class CreateAudienceRequest(BaseModel):
    """Create audience request"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    audience_type: str = Field("static", description="static or dynamic")
    query: Optional[Dict[str, Any]] = Field(None, description="For dynamic audiences")
    tags: List[str] = Field(default_factory=list)


class ContactRequest(BaseModel):
    """Single contact"""
    phone_number: str = Field(..., description="Phone in E.164 format (+919876543210)")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AddContactsRequest(BaseModel):
    """Add multiple contacts"""
    contacts: List[ContactRequest]


class AudienceResponse(BaseModel):
    """Audience response"""
    id: UUID
    tenant_id: UUID
    name: str
    audience_type: str
    status: str
    description: Optional[str]
    tags: List[str]
    total_contacts: int
    valid_contacts: int
    invalid_contacts: int
    query: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime]


class AudienceListResponse(BaseModel):
    """Paginated audience list"""
    items: List[AudienceResponse]
    total: int
    limit: int
    offset: int


class ContactResponse(BaseModel):
    """Contact response"""
    phone_number: str
    metadata: Dict[str, Any]


class ContactListResponse(BaseModel):
    """Paginated contact list"""
    items: List[ContactResponse]
    total: int
    limit: int
    offset: int


class AudienceStatsResponse(BaseModel):
    """Audience statistics"""
    total_contacts: int
    valid_contacts: int
    invalid_contacts: int
    rcs_capable: Optional[int] = None
    sms_only: Optional[int] = None
    opted_out: Optional[int] = None


class CSVUploadResponse(BaseModel):
    """CSV upload result"""
    imported: int
    skipped: int
    total_contacts: int
    errors: List[str]


# Helper Functions

async def get_audience_or_404(
    audience_id: UUID,
    tenant_id: UUID,
    session: AsyncSession,
) -> Audience:
    """Get audience or raise 404"""
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select
    
    stmt = select(AudienceModel).where(
        AudienceModel.id == audience_id,
        AudienceModel.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audience not found",
        )
    
    # Convert to domain model
    audience = Audience(
        id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        audience_type=AudienceType(model.audience_type),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
    
    audience.status = AudienceStatus(model.status)
    audience.description = model.description
    audience.tags = model.tags or []
    audience.query = model.query
    audience.total_contacts = model.total_contacts
    audience.valid_contacts = model.valid_contacts
    audience.invalid_contacts = model.invalid_contacts
    audience.last_used_at = model.last_used_at
    
    # Load contacts if static
    if model.contacts:
        for contact_data in model.contacts:
            contact = Contact(
                phone_number=contact_data["phone_number"],
                metadata=contact_data.get("metadata", {}),
            )
            audience.contacts.append(contact)
    
    return audience


async def save_audience(audience: Audience, session: AsyncSession):
    """Save audience to database"""
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select
    
    # Check if exists
    stmt = select(AudienceModel).where(AudienceModel.id == audience.id)
    result = await session.execute(stmt)
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
        # Update
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
        # Create
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
        session.add(model)
    
    await session.commit()


# API Endpoints

@router.post("", response_model=AudienceResponse, status_code=status.HTTP_201_CREATED)
async def create_audience(
    request: CreateAudienceRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Create new audience"""
    
    # Create audience
    if request.audience_type == "dynamic" and request.query:
        audience = Audience.create_dynamic(
            tenant_id=tenant_id,
            name=request.name,
            query=request.query,
        )
    else:
        audience = Audience.create(
            tenant_id=tenant_id,
            name=request.name,
            audience_type=AudienceType.STATIC,
        )
    
    audience.description = request.description
    audience.tags = request.tags
    
    # Save
    await save_audience(audience, session)
    
    return AudienceResponse(
        id=audience.id,
        tenant_id=audience.tenant_id,
        name=audience.name,
        audience_type=audience.audience_type.value,
        status=audience.status.value,
        description=audience.description,
        tags=audience.tags,
        total_contacts=audience.total_contacts,
        valid_contacts=audience.valid_contacts,
        invalid_contacts=audience.invalid_contacts,
        query=audience.query,
        created_at=audience.created_at,
        updated_at=audience.updated_at,
        last_used_at=audience.last_used_at,
    )


@router.get("", response_model=AudienceListResponse)
async def list_audiences(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    audience_type: Optional[str] = None,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """List audiences with pagination"""
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select, func
    
    # Build query
    stmt = select(AudienceModel).where(AudienceModel.tenant_id == tenant_id)
    
    if status:
        stmt = stmt.where(AudienceModel.status == status)
    if audience_type:
        stmt = stmt.where(AudienceModel.audience_type == audience_type)
    
    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar()
    
    # Get results
    stmt = stmt.order_by(AudienceModel.created_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    
    result = await session.execute(stmt)
    models = result.scalars().all()
    
    items = [
        AudienceResponse(
            id=m.id,
            tenant_id=m.tenant_id,
            name=m.name,
            audience_type=m.audience_type,
            status=m.status,
            description=m.description,
            tags=m.tags or [],
            total_contacts=m.total_contacts,
            valid_contacts=m.valid_contacts,
            invalid_contacts=m.invalid_contacts,
            query=m.query,
            created_at=m.created_at,
            updated_at=m.updated_at,
            last_used_at=m.last_used_at,
        )
        for m in models
    ]
    
    return AudienceListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{audience_id}", response_model=AudienceResponse)
async def get_audience(
    audience_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Get audience by ID"""
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    return AudienceResponse(
        id=audience.id,
        tenant_id=audience.tenant_id,
        name=audience.name,
        audience_type=audience.audience_type.value,
        status=audience.status.value,
        description=audience.description,
        tags=audience.tags,
        total_contacts=audience.total_contacts,
        valid_contacts=audience.valid_contacts,
        invalid_contacts=audience.invalid_contacts,
        query=audience.query,
        created_at=audience.created_at,
        updated_at=audience.updated_at,
        last_used_at=audience.last_used_at,
    )


@router.post("/{audience_id}/upload", response_model=CSVUploadResponse)
async def upload_csv(
    audience_id: UUID,
    file: UploadFile = File(..., description="CSV file with phone numbers"),
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Upload CSV file with contacts
    
    CSV format:
    - Must have 'phone' or 'phone_number' column
    - Optional: name, email, any custom fields
    - First row must be headers
    
    Example:
    ```csv
    phone,name,email
    +919876543210,John Doe,john@example.com
    +919876543211,Jane Smith,jane@example.com
    ```
    """
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    # Check if static
    if audience.audience_type != AudienceType.STATIC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only upload CSV to static audiences",
        )
    
    # Read CSV
    try:
        contents = await file.read()
        decoded = contents.decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(decoded))
        
        rows = list(csv_reader)
        
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV file is empty",
            )
        
        # Import
        result = audience.import_from_csv(rows)
        
        # Save
        await save_audience(audience, session)
        
        return CSVUploadResponse(**result)
        
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be UTF-8 encoded CSV",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV processing error: {str(e)}",
        )


@router.post("/{audience_id}/contacts")
async def add_contacts(
    audience_id: UUID,
    request: AddContactsRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Add contacts manually"""
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    # Convert to domain contacts
    contacts = [
        Contact(phone_number=c.phone_number, metadata=c.metadata)
        for c in request.contacts
    ]
    
    # Add
    audience.add_contacts(contacts)
    
    # Save
    await save_audience(audience, session)
    
    return {
        "added": len(contacts),
        "total_contacts": audience.total_contacts,
    }


@router.get("/{audience_id}/contacts", response_model=ContactListResponse)
async def get_contacts(
    audience_id: UUID,
    limit: int = 100,
    offset: int = 0,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Get contacts from audience"""
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    contacts = audience.get_contacts(limit=limit, offset=offset)
    
    items = [
        ContactResponse(
            phone_number=c.phone_number,
            metadata=c.metadata,
        )
        for c in contacts
    ]
    
    return ContactListResponse(
        items=items,
        total=audience.total_contacts,
        limit=limit,
        offset=offset,
    )


@router.get("/{audience_id}/stats", response_model=AudienceStatsResponse)
async def get_stats(
    audience_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Get audience statistics"""
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    # TODO: Check RCS capability, opt-out status
    # For now, return basic stats
    
    return AudienceStatsResponse(
        total_contacts=audience.total_contacts,
        valid_contacts=audience.valid_contacts,
        invalid_contacts=audience.invalid_contacts,
    )


@router.delete("/{audience_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audience(
    audience_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Delete audience"""
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import delete
    
    # Check if exists
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    
    # Delete
    stmt = delete(AudienceModel).where(AudienceModel.id == audience_id)
    await session.execute(stmt)
    await session.commit()
    
    return None
