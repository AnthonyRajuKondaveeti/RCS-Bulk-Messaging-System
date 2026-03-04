"""
Campaign API Routes (v1)

RESTful endpoints for campaign management.

Endpoints:
    POST   /campaigns                    - Create campaign
    GET    /campaigns                    - List campaigns
    GET    /campaigns/{id}               - Get campaign
    POST   /campaigns/{id}/schedule      - Schedule campaign
    POST   /campaigns/{id}/activate      - Activate campaign (sends immediately)
    POST   /campaigns/{id}/pause         - Pause campaign
    POST   /campaigns/{id}/cancel        - Cancel campaign
    DELETE /campaigns/{id}               - Delete campaign (draft only)

Notes:
    - template_id must reference an APPROVED template (status="approved")
    - template must have external_template_id (rcssms.in template ID string)
    - campaign.metadata must include audience_ids list before activating
"""

from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.adapters.db.postgres import get_db_session
from apps.adapters.db.unit_of_work import SQLAlchemyUnitOfWork
from apps.adapters.queue.rabbitmq import RabbitMQAdapter
from apps.core.domain.campaign import CampaignStatus, CampaignType, Priority
from apps.core.services.campaign_service import CampaignService
from apps.core.config import get_settings
from apps.api.middleware.auth import get_current_tenant


router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


# ── Request / Response models ──────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    """Request to create a campaign"""
    name: str = Field(..., min_length=1, max_length=255)
    template_id: UUID                           # must be an approved template UUID
    campaign_type: CampaignType
    priority: Priority = Priority.MEDIUM
    description: Optional[str] = None
    tags: List[str] = []
    audience_ids: List[UUID] = Field(
        default_factory=list,
        description="UUIDs of audience segments to include in this campaign",
    )


class ScheduleCampaignRequest(BaseModel):
    """Request to schedule a campaign"""
    scheduled_for: datetime = Field(..., description="ISO 8601 datetime (UTC)")


class CancelCampaignRequest(BaseModel):
    """Request to cancel a campaign"""
    reason: str = Field(..., min_length=1)


class CampaignStatsResponse(BaseModel):
    recipient_count: int
    messages_sent: int
    messages_delivered: int
    messages_failed: int
    messages_read: int
    fallback_triggered: int
    delivery_rate: float


class CampaignResponse(BaseModel):
    """Campaign response"""
    id: UUID
    tenant_id: UUID
    name: str
    status: CampaignStatus
    campaign_type: CampaignType
    template_id: UUID
    priority: Priority
    scheduled_for: Optional[datetime]
    stats: CampaignStatsResponse
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignListResponse(BaseModel):
    """Paginated campaign list"""
    campaigns: List[CampaignResponse]
    total: int
    limit: int
    offset: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def _campaign_to_response(campaign) -> CampaignResponse:
    """Convert Campaign domain object to API response"""
    return CampaignResponse(
        id=campaign.id,
        tenant_id=campaign.tenant_id,
        name=campaign.name,
        status=campaign.status,
        campaign_type=campaign.campaign_type,
        template_id=campaign.template_id,
        priority=campaign.priority,
        scheduled_for=campaign.scheduled_for,
        stats=CampaignStatsResponse(
            recipient_count=campaign.recipient_count,
            messages_sent=campaign.stats.messages_sent,
            messages_delivered=campaign.stats.messages_delivered,
            messages_failed=campaign.stats.messages_failed,
            messages_read=getattr(campaign.stats, "messages_read", 0),
            fallback_triggered=getattr(campaign.stats, "fallback_triggered", 0),
            delivery_rate=campaign.stats.delivery_rate,
        ),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


async def _get_queue():
    """Get RabbitMQ queue connection"""
    settings = get_settings()
    queue = RabbitMQAdapter(url=settings.rabbitmq.url)
    await queue.connect()
    return queue


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    request: CreateCampaignRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Create a new campaign in DRAFT status.

    Provide audience_ids in the request to link audiences.
    Activate or schedule after creation to trigger sending.
    """
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)

        # Verify template exists and is approved
        template = await uow.templates.get_by_id(request.template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {request.template_id} not found",
            )

        if template.status != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Template must be approved before use in campaigns. "
                    f"Current status: {template.status}"
                ),
            )

        if not getattr(template, 'external_template_id', None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Template does not have an external_template_id. "
                    "Submit the template to rcssms.in and store the returned "
                    "templateid as external_template_id before using in campaigns."
                ),
            )

        service = CampaignService(uow, queue)

        metadata = {}
        if request.audience_ids:
            metadata["audience_ids"] = [str(aid) for aid in request.audience_ids]
        if request.description:
            metadata["description"] = request.description
        if request.tags:
            metadata["tags"] = request.tags

        campaign = await service.create_campaign(
            tenant_id=tenant_id,
            name=request.name,
            template_id=request.template_id,
            campaign_type=request.campaign_type,
            priority=request.priority,
            metadata=metadata,
        )

        return _campaign_to_response(campaign)

    finally:
        await queue.close()


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    status_filter: Optional[CampaignStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """List campaigns with optional status filter."""
    from apps.adapters.db.models import CampaignModel
    from sqlalchemy import select, func
    
    uow = SQLAlchemyUnitOfWork(session)
    queue = await _get_queue()

    try:
        # Build base query for counting
        count_stmt = select(CampaignModel).where(
            CampaignModel.tenant_id == tenant_id
        )
        if status_filter:
            count_stmt = count_stmt.where(CampaignModel.status == status_filter)
        
        # Get total count
        total_stmt = select(func.count()).select_from(count_stmt.subquery())
        total_result = await session.execute(total_stmt)
        total = total_result.scalar()

        # Get paginated campaigns
        service = CampaignService(uow, queue)
        campaigns = await service.list_campaigns(
            tenant_id=tenant_id,
            status=status_filter,
            limit=limit,
            offset=offset,
        )

        return CampaignListResponse(
            campaigns=[_campaign_to_response(c) for c in campaigns],
            total=total,
            limit=limit,
            offset=offset,
        )
    finally:
        await queue.close()


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Get campaign by ID."""
    uow = SQLAlchemyUnitOfWork(session)
    queue = await _get_queue()

    try:
        service = CampaignService(uow, queue)
        campaign = await service.get_campaign(campaign_id)

        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        return _campaign_to_response(campaign)
    finally:
        await queue.close()


@router.post("/{campaign_id}/schedule", response_model=CampaignResponse)
async def schedule_campaign(
    campaign_id: UUID,
    request: ScheduleCampaignRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Schedule campaign for future execution.

    Campaign must be in DRAFT status.
    Ensure audience_ids are set in campaign metadata before scheduling.
    """
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        campaign = await service.schedule_campaign(
            campaign_id=campaign_id,
            scheduled_for=request.scheduled_for,
        )

        return _campaign_to_response(campaign)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        await queue.close()


@router.post("/{campaign_id}/activate", response_model=CampaignResponse)
async def activate_campaign(
    campaign_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Activate campaign for immediate execution.

    Campaign must be in DRAFT or SCHEDULED status.
    Triggers the campaign orchestrator to start sending messages.
    """
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        # Check audience_ids are set
        if not campaign.metadata.get("audience_ids"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Campaign has no audience_ids. "
                    "Create the campaign with audience_ids or update metadata before activating."
                ),
            )

        campaign = await service.activate_campaign(campaign_id=campaign_id)

        return _campaign_to_response(campaign)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        await queue.close()


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
async def pause_campaign(
    campaign_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Pause an active campaign."""
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        campaign = await service.pause_campaign(campaign_id=campaign_id)

        return _campaign_to_response(campaign)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        await queue.close()


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
async def resume_campaign(
    campaign_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Resume a paused campaign."""
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        if campaign.status != CampaignStatus.PAUSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Campaign must be paused to resume. Current status: {campaign.status.value}",
            )

        campaign = await service.resume_campaign(campaign_id=campaign_id)

        return _campaign_to_response(campaign)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        await queue.close()


@router.post("/{campaign_id}/cancel", response_model=CampaignResponse)
async def cancel_campaign(
    campaign_id: UUID,
    request: CancelCampaignRequest,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Cancel a campaign."""
    queue = await _get_queue()

    try:
        uow = SQLAlchemyUnitOfWork(session)
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        campaign = await service.cancel_campaign(
            campaign_id=campaign_id,
            reason=request.reason,
        )

        return _campaign_to_response(campaign)

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    finally:
        await queue.close()


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """Delete campaign (DRAFT status only)."""
    uow = SQLAlchemyUnitOfWork(session)
    queue = await _get_queue()

    try:
        service = CampaignService(uow, queue)

        campaign = await service.get_campaign(campaign_id)
        if not campaign or campaign.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )

        if campaign.status != CampaignStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only DRAFT campaigns can be deleted",
            )

        await uow.campaigns.delete(campaign_id)
        await session.commit()

    finally:
        await queue.close()
