"""
Template API Routes

RESTful API for RCS message templates with approval workflow.

Features:
    - Create/Read/Update/Delete templates
    - Variable management
    - Rich card templates
    - Suggested actions (reply, URL, dial buttons)
    - Template approval workflow (draft → pending → approved)
    - Render preview with test data
    
Endpoints:
    POST   /templates              - Create new template
    GET    /templates              - List templates (paginated)
    GET    /templates/{id}         - Get template by ID
    PUT    /templates/{id}         - Update template
    DELETE /templates/{id}         - Delete template (draft only)
    POST   /templates/{id}/submit  - Submit for approval
    POST   /templates/{id}/approve - Approve template
    POST   /templates/{id}/reject  - Reject template
    POST   /templates/{id}/preview - Preview with test data
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field

from apps.adapters.db.postgres import get_db_session
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.core.domain.template import Template, TemplateStatus, TemplateVariable
from apps.core.domain.message import RichCard, SuggestedAction
from apps.api.middleware.auth import get_current_tenant
from apps.api.middleware.tenancy import validate_tenant_access
from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/templates", tags=["Templates"])


# Request/Response Models

class TemplateVariableRequest(BaseModel):
    """Template variable definition"""
    name: str = Field(..., description="Variable name (e.g., customer_name)")
    description: Optional[str] = Field(None, description="Variable description")
    required: bool = Field(True, description="Is this variable required?")
    default_value: Optional[str] = Field(None, description="Default value if not provided")
    validation_regex: Optional[str] = Field(None, description="Regex for validation")


class RichCardRequest(BaseModel):
    """Rich card template"""
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    media_url: Optional[str] = None
    media_type: Optional[str] = Field(None, description="image/jpeg, video/mp4, etc.")


class SuggestedActionRequest(BaseModel):
    """Suggested action (button)"""
    type: str = Field(..., description="reply, url, or dial")
    text: str = Field(..., max_length=25, description="Button text")
    postback_data: Optional[str] = Field(None, description="For reply buttons")
    url: Optional[str] = Field(None, description="For URL buttons")
    phone_number: Optional[str] = Field(None, description="For dial buttons")


class CreateTemplateRequest(BaseModel):
    """Create template request"""
    name: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=1024, description="Text with {{variables}}")
    description: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, max_length=100)
    language: str = Field("en", description="Language code")
    variables: List[TemplateVariableRequest] = Field(default_factory=list)
    rich_card: Optional[RichCardRequest] = None
    suggestions: List[SuggestedActionRequest] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class UpdateTemplateRequest(BaseModel):
    """Update template request (draft only)"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1, max_length=1024)
    description: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    variables: Optional[List[TemplateVariableRequest]] = None
    rich_card: Optional[RichCardRequest] = None
    suggestions: Optional[List[SuggestedActionRequest]] = None
    tags: Optional[List[str]] = None


class PreviewRequest(BaseModel):
    """Preview template with test data"""
    variable_values: Dict[str, str] = Field(default_factory=dict)


class TemplateResponse(BaseModel):
    """Template response"""
    id: UUID
    tenant_id: UUID
    name: str
    status: str
    content: str
    description: Optional[str]
    category: Optional[str]
    language: str
    variables: List[Dict[str, Any]]
    rich_card: Optional[Dict[str, Any]]
    suggestions: List[Dict[str, Any]]
    tags: List[str]
    usage_count: int
    last_used_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class TemplateListResponse(BaseModel):
    """Paginated template list"""
    items: List[TemplateResponse]
    total: int
    limit: int
    offset: int


# Helper functions

def template_variable_to_dict(var: TemplateVariable) -> Dict[str, Any]:
    """Convert TemplateVariable to dict"""
    return {
        "name": var.name,
        "description": var.description,
        "required": var.required,
        "default_value": var.default_value,
        "validation_regex": var.validation_regex,
    }


def rich_card_to_dict(card: RichCard) -> Dict[str, Any]:
    """Convert RichCard to dict"""
    return {
        "title": card.title,
        "description": card.description,
        "media_url": card.media_url,
        "media_type": card.media_type,
    }


def suggestion_to_dict(action: SuggestedAction) -> Dict[str, Any]:
    """Convert SuggestedAction to dict"""
    return {
        "type": action.type,
        "text": action.text,
        "postback_data": action.postback_data,
        "url": action.url,
        "phone_number": action.phone_number,
    }


async def get_template_or_404(
    template_id: UUID,
    tenant_id: UUID,
    session: AsyncSession,
) -> Template:
    """Get template or raise 404"""
    uow = SQLAlchemyUnitOfWork(session)
    
    # Get template
    from apps.adapters.db.models import TemplateModel
    from sqlalchemy import select
    
    stmt = select(TemplateModel).where(
        TemplateModel.id == template_id,
        TemplateModel.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )
    
    # Convert to domain model
    template = Template(
        id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        content=model.content,
        status=TemplateStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
    
    # Set additional fields
    template.description = model.description
    template.category = model.category
    template.language = model.language
    template.tags = model.tags or []
    template.usage_count = model.usage_count
    template.last_used_at = model.last_used_at
    
    # Set variables
    for var_data in model.variables or []:
        var = TemplateVariable(
            name=var_data["name"],
            description=var_data.get("description"),
            required=var_data.get("required", True),
            default_value=var_data.get("default_value"),
            validation_regex=var_data.get("validation_regex"),
        )
        template.variables.append(var)
    
    # Set rich card
    if model.rich_card_template:
        template.rich_card_template = RichCard(
            title=model.rich_card_template["title"],
            description=model.rich_card_template.get("description"),
            media_url=model.rich_card_template.get("media_url"),
            media_type=model.rich_card_template.get("media_type"),
        )
    
    # Set suggestions
    for sug_data in model.suggestions_template or []:
        suggestion = SuggestedAction(
            type=sug_data["type"],
            text=sug_data["text"],
            postback_data=sug_data.get("postback_data"),
            url=sug_data.get("url"),
            phone_number=sug_data.get("phone_number"),
        )
        template.suggestions_template.append(suggestion)
    
    return template


# API Endpoints

@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    request: CreateTemplateRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create new template
    
    Creates template in DRAFT status. Must be submitted for approval before use.
    """
    uow = SQLAlchemyUnitOfWork(session)
    
    # Create template
    template = Template.create(
        tenant_id=tenant_id,
        name=request.name,
        content=request.content,
        variables=[var.name for var in request.variables],
    )
    
    # Set optional fields
    template.description = request.description
    template.category = request.category
    template.language = request.language
    template.tags = request.tags
    
    # Add variables with metadata
    template.variables = []
    for var in request.variables:
        template_var = TemplateVariable(
            name=var.name,
            description=var.description,
            required=var.required,
            default_value=var.default_value,
            validation_regex=var.validation_regex,
        )
        template.variables.append(template_var)
    
    # Add rich card
    if request.rich_card:
        template.set_rich_card(
            title=request.rich_card.title,
            description=request.rich_card.description,
            media_url=request.rich_card.media_url,
            media_type=request.rich_card.media_type,
        )
    
    # Add suggestions
    for sug in request.suggestions:
        template.add_suggestion(
            type=sug.type,
            text=sug.text,
            postback_data=sug.postback_data,
            url=sug.url,
            phone_number=sug.phone_number,
        )
    
    # Save to database
    from apps.adapters.db.models import TemplateModel
    
    model = TemplateModel(
        id=template.id,
        tenant_id=template.tenant_id,
        name=template.name,
        status=template.status.value,
        content=template.content,
        description=template.description,
        category=template.category,
        language=template.language,
        tags=template.tags,
        variables=[template_variable_to_dict(v) for v in template.variables],
        rich_card_template=rich_card_to_dict(template.rich_card_template) if template.rich_card_template else None,
        suggestions_template=[suggestion_to_dict(s) for s in template.suggestions_template],
        usage_count=0,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )
    
    session.add(model)
    await session.commit()
    
    # Build response
    return TemplateResponse(
        id=template.id,
        tenant_id=template.tenant_id,
        name=template.name,
        status=template.status.value,
        content=template.content,
        description=template.description,
        category=template.category,
        language=template.language,
        variables=[template_variable_to_dict(v) for v in template.variables],
        rich_card=rich_card_to_dict(template.rich_card_template) if template.rich_card_template else None,
        suggestions=[suggestion_to_dict(s) for s in template.suggestions_template],
        tags=template.tags,
        usage_count=template.usage_count,
        last_used_at=template.last_used_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    category: Optional[str] = None,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    List templates with pagination and filtering
    
    Query params:
    - limit: Max results (default 100)
    - offset: Pagination offset
    - status: Filter by status (draft, pending_approval, approved, rejected)
    - category: Filter by category
    """
    from apps.adapters.db.models import TemplateModel
    from sqlalchemy import select, func
    
    # Build query
    stmt = select(TemplateModel).where(TemplateModel.tenant_id == tenant_id)
    
    if status:
        stmt = stmt.where(TemplateModel.status == status)
    if category:
        stmt = stmt.where(TemplateModel.category == category)
    
    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar()
    
    # Get paginated results
    stmt = stmt.order_by(TemplateModel.created_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    
    result = await session.execute(stmt)
    models = result.scalars().all()
    
    # Build responses
    items = []
    for model in models:
        items.append(TemplateResponse(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            status=model.status,
            content=model.content,
            description=model.description,
            category=model.category,
            language=model.language,
            variables=model.variables or [],
            rich_card=model.rich_card_template,
            suggestions=model.suggestions_template or [],
            tags=model.tags or [],
            usage_count=model.usage_count,
            last_used_at=model.last_used_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        ))
    
    return TemplateListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Get template by ID"""
    template = await get_template_or_404(template_id, tenant_id, session)
    
    return TemplateResponse(
        id=template.id,
        tenant_id=template.tenant_id,
        name=template.name,
        status=template.status.value,
        content=template.content,
        description=template.description,
        category=template.category,
        language=template.language,
        variables=[template_variable_to_dict(v) for v in template.variables],
        rich_card=rich_card_to_dict(template.rich_card_template) if template.rich_card_template else None,
        suggestions=[suggestion_to_dict(s) for s in template.suggestions_template],
        tags=template.tags,
        usage_count=template.usage_count,
        last_used_at=template.last_used_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.post("/{template_id}/submit", response_model=TemplateResponse)
async def submit_for_approval(
    template_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Submit template for approval
    
    Changes status from DRAFT to PENDING_APPROVAL.
    """
    template = await get_template_or_404(template_id, tenant_id, session)
    
    # Submit for approval
    template.submit_for_approval()
    
    # Update in database
    from apps.adapters.db.models import TemplateModel
    from sqlalchemy import update
    
    stmt = update(TemplateModel).where(
        TemplateModel.id == template_id
    ).values(
        status=template.status.value,
        updated_at=datetime.utcnow(),
    )
    
    await session.execute(stmt)
    await session.commit()
    
    return TemplateResponse(
        id=template.id,
        tenant_id=template.tenant_id,
        name=template.name,
        status=template.status.value,
        content=template.content,
        description=template.description,
        category=template.category,
        language=template.language,
        variables=[template_variable_to_dict(v) for v in template.variables],
        rich_card=rich_card_to_dict(template.rich_card_template) if template.rich_card_template else None,
        suggestions=[suggestion_to_dict(s) for s in template.suggestions_template],
        tags=template.tags,
        usage_count=template.usage_count,
        last_used_at=template.last_used_at,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.post("/{template_id}/preview")
async def preview_template(
    template_id: UUID,
    preview_request: PreviewRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Preview template with test data
    
    Renders template with provided variable values.
    """
    template = await get_template_or_404(template_id, tenant_id, session)
    
    # Render template
    try:
        rendered = template.render(preview_request.variable_values)
        
        return {
            "text": rendered.text,
            "rich_card": rich_card_to_dict(rendered.rich_card) if rendered.rich_card else None,
            "suggestions": [suggestion_to_dict(s) for s in rendered.suggestions],
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Delete template (draft only)
    
    Only templates in DRAFT status can be deleted.
    """
    template = await get_template_or_404(template_id, tenant_id, session)
    
    # Check if draft
    if template.status != TemplateStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft templates can be deleted",
        )
    
    # Delete from database
    from apps.adapters.db.models import TemplateModel
    from sqlalchemy import delete
    
    stmt = delete(TemplateModel).where(TemplateModel.id == template_id)
    await session.execute(stmt)
    await session.commit()
    
    return None
