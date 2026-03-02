"""
Audience API Routes

FIX: ContactRequest now accepts a 'variables' field (ordered list matching
     template placeholders). Previously contacts only had phone_number + metadata,
     so campaigns always sent messages with empty variables.

     audience_repo and audience_api save_audience() also updated to persist variables.

Endpoints:
    POST   /audiences                   - Create audience
    GET    /audiences                   - List audiences
    GET    /audiences/{id}              - Get audience
    DELETE /audiences/{id}              - Delete audience
    POST   /audiences/{id}/upload       - Upload CSV with variables columns
    POST   /audiences/{id}/contacts     - Add contacts manually
    GET    /audiences/{id}/contacts     - Get contacts (paginated)
    GET    /audiences/{id}/stats        - Get statistics
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


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateAudienceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    audience_type: str = Field("static", description="static or dynamic")
    query: Optional[Dict[str, Any]] = Field(None, description="For dynamic audiences")
    tags: List[str] = Field(default_factory=list)


class ContactRequest(BaseModel):
    """
    Single contact entry.

    FIX: Added 'variables' field — ordered list of values that match the
    campaign template's placeholders.  The orchestrator reads contact.variables
    when building MessageContent for each recipient.

    Example for template "Hi {{1}}, your order {{2}} is ready":
        phone_number: "+919876543210"
        variables:    ["John", "ORD-1234"]
        metadata:     {"email": "john@example.com"}    # optional extras
    """
    phone_number: str = Field(..., description="Phone in E.164 format (+919876543210)")
    variables: List[Any] = Field(
        default_factory=list,
        description="Ordered variable values matching the campaign template placeholders",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AddContactsRequest(BaseModel):
    contacts: List[ContactRequest]


class AudienceResponse(BaseModel):
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
    items: List[AudienceResponse]
    total: int
    limit: int
    offset: int


class ContactResponse(BaseModel):
    phone_number: str
    variables: List[Any]
    metadata: Dict[str, Any]


class ContactListResponse(BaseModel):
    items: List[ContactResponse]
    total: int
    limit: int
    offset: int


class AudienceStatsResponse(BaseModel):
    total_contacts: int
    valid_contacts: int
    invalid_contacts: int
    rcs_capable: Optional[int] = None
    sms_only: Optional[int] = None
    opted_out: Optional[int] = None


class CSVUploadResponse(BaseModel):
    imported: int
    skipped: int
    total_contacts: int
    errors: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_audience_or_404(
    audience_id: UUID,
    tenant_id: UUID,
    session: AsyncSession,
) -> Audience:
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select

    stmt = select(AudienceModel).where(
        AudienceModel.id == audience_id,
        AudienceModel.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Audience not found")

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

    # Load contacts — include variables
    if model.contacts:
        for cd in model.contacts:
            contact = Contact(
                phone_number=cd["phone_number"],
                metadata=cd.get("metadata", {}),
            )
            # Attach variables as an attribute (Contact domain may not have it natively)
            contact.variables = cd.get("variables", [])
            audience.contacts.append(contact)

    return audience


async def save_audience(audience: Audience, session: AsyncSession) -> None:
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select

    stmt = select(AudienceModel).where(AudienceModel.id == audience.id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    # Serialize contacts — include variables
    contacts_data = [
        {
            "phone_number": c.phone_number,
            "variables": getattr(c, "variables", []),
            "metadata": c.metadata,
        }
        for c in audience.contacts
    ]

    if existing:
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
        existing.updated_at = datetime.utcnow()
    else:
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


def _to_response(audience: Audience) -> AudienceResponse:
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=AudienceResponse, status_code=status.HTTP_201_CREATED)
async def create_audience(
    request: CreateAudienceRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    if request.audience_type == "dynamic" and request.query:
        audience = Audience.create_dynamic(
            tenant_id=tenant_id, name=request.name, query=request.query)
    else:
        audience = Audience.create(
            tenant_id=tenant_id, name=request.name, audience_type=AudienceType.STATIC)
    audience.description = request.description
    audience.tags = request.tags
    await save_audience(audience, session)
    return _to_response(audience)


@router.get("", response_model=AudienceListResponse)
async def list_audiences(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    audience_type: Optional[str] = None,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import select, func

    stmt = select(AudienceModel).where(AudienceModel.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(AudienceModel.status == status)
    if audience_type:
        stmt = stmt.where(AudienceModel.audience_type == audience_type)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar()

    stmt = stmt.order_by(AudienceModel.created_at.desc()).limit(limit).offset(offset)
    models = (await session.execute(stmt)).scalars().all()

    items = [
        AudienceResponse(
            id=m.id, tenant_id=m.tenant_id, name=m.name,
            audience_type=m.audience_type, status=m.status,
            description=m.description, tags=m.tags or [],
            total_contacts=m.total_contacts, valid_contacts=m.valid_contacts,
            invalid_contacts=m.invalid_contacts, query=m.query,
            created_at=m.created_at, updated_at=m.updated_at,
            last_used_at=m.last_used_at,
        )
        for m in models
    ]
    return AudienceListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{audience_id}", response_model=AudienceResponse)
async def get_audience(
    audience_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    return _to_response(await get_audience_or_404(audience_id, tenant_id, session))


@router.post("/{audience_id}/upload", response_model=CSVUploadResponse)
async def upload_csv(
    audience_id: UUID,
    file: UploadFile = File(..., description="CSV file with phone numbers"),
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Upload CSV file with contacts.

    CSV format:
        phone_number  — required
        var_1, var_2, … var_N  — optional, mapped to variables[] in order
        any other columns      — stored in metadata

    Example:
        phone_number,var_1,var_2,name
        +919876543210,John,ORD-1234,John Doe
    """
    audience = await get_audience_or_404(audience_id, tenant_id, session)

    if audience.audience_type != AudienceType.STATIC:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Can only upload CSV to static audiences")

    try:
        contents = await file.read()
        decoded = contents.decode("utf-8")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="File must be UTF-8 encoded CSV")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"CSV error: {e}")

    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="CSV file is empty")

    imported = 0
    skipped = 0
    errors = []

    for i, row in enumerate(rows, start=2):  # row 1 = header
        phone = row.get("phone_number") or row.get("phone") or ""
        if not phone:
            errors.append(f"Row {i}: missing phone_number")
            skipped += 1
            continue

        # Extract ordered variables from var_1, var_2, … columns
        variables = []
        j = 1
        while f"var_{j}" in row:
            variables.append(row[f"var_{j}"])
            j += 1

        # Everything else goes into metadata
        skip_cols = {"phone_number", "phone"} | {f"var_{k}" for k in range(1, j)}
        metadata = {k: v for k, v in row.items() if k not in skip_cols and v}

        contact = Contact(phone_number=phone, metadata=metadata)
        contact.variables = variables
        audience.contacts.append(contact)
        imported += 1

    audience.total_contacts = len(audience.contacts)
    audience.valid_contacts = imported
    audience.invalid_contacts = skipped
    await save_audience(audience, session)

    return CSVUploadResponse(
        imported=imported,
        skipped=skipped,
        total_contacts=audience.total_contacts,
        errors=errors,
    )


@router.post("/{audience_id}/contacts")
async def add_contacts(
    audience_id: UUID,
    request: AddContactsRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    audience = await get_audience_or_404(audience_id, tenant_id, session)

    for c in request.contacts:
        contact = Contact(phone_number=c.phone_number, metadata=c.metadata)
        contact.variables = c.variables      # FIX: persist variables
        audience.contacts.append(contact)

    audience.total_contacts = len(audience.contacts)
    audience.valid_contacts = audience.total_contacts
    await save_audience(audience, session)

    return {"added": len(request.contacts), "total_contacts": audience.total_contacts}


@router.get("/{audience_id}/contacts", response_model=ContactListResponse)
async def get_contacts(
    audience_id: UUID,
    limit: int = 100,
    offset: int = 0,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    audience = await get_audience_or_404(audience_id, tenant_id, session)
    page = audience.contacts[offset: offset + limit]
    items = [
        ContactResponse(
            phone_number=c.phone_number,
            variables=getattr(c, "variables", []),
            metadata=c.metadata,
        )
        for c in page
    ]
    return ContactListResponse(items=items, total=audience.total_contacts,
                               limit=limit, offset=offset)


@router.get("/{audience_id}/stats", response_model=AudienceStatsResponse)
async def get_stats(
    audience_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    audience = await get_audience_or_404(audience_id, tenant_id, session)
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
    from apps.adapters.db.models import AudienceModel
    from sqlalchemy import delete

    await get_audience_or_404(audience_id, tenant_id, session)
    await session.execute(delete(AudienceModel).where(AudienceModel.id == audience_id))
    await session.commit()
