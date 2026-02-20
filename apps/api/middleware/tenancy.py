"""
Tenancy Middleware

Enforces multi-tenant data isolation.
Ensures all database queries are scoped to the authenticated tenant.

Features:
    - Automatic tenant_id injection
    - Cross-tenant access prevention
    - Tenant-scoped queries
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)


class TenancyMiddleware(BaseHTTPMiddleware):
    """
    Multi-tenancy middleware
    
    Ensures all database operations are scoped to the authenticated tenant.
    Prevents accidental cross-tenant data access.
    
    Note:
        This middleware should run AFTER AuthMiddleware
    """
    
    async def dispatch(self, request: Request, call_next):
        """Enforce tenancy context"""
        
        # Skip for public endpoints
        if not hasattr(request.state, "authenticated"):
            return await call_next(request)
        
        # Validate tenant context exists
        if not hasattr(request.state, "tenant_id"):
            logger.error("Authenticated request missing tenant_id")
            return await self._error_response(
                "Internal error: Missing tenant context"
            )
        
        tenant_id = request.state.tenant_id
        
        # Log tenant context for debugging
        logger.debug(
            f"Request from tenant {tenant_id}: "
            f"{request.method} {request.url.path}"
        )
        
        # Add tenant context to all queries
        # This is accessed by repositories to filter data
        request.state.tenant_context = {
            "tenant_id": tenant_id,
            "isolation_enabled": True,
        }
        
        # Process request
        response = await call_next(request)
        
        # Add tenant header to response (for debugging)
        response.headers["X-Tenant-ID"] = str(tenant_id)
        
        return response
    
    async def _error_response(self, message: str):
        """Return error response"""
        from starlette.responses import JSONResponse
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": message},
        )


def get_tenant_context(request: Request) -> dict:
    """
    Get tenant context from request
    
    Usage in repositories:
        tenant_id = get_tenant_context(request)["tenant_id"]
        query = query.filter(Model.tenant_id == tenant_id)
    """
    if not hasattr(request.state, "tenant_context"):
        raise ValueError("Tenant context not available")
    
    return request.state.tenant_context


def validate_tenant_access(
    request: Request,
    resource_tenant_id: UUID,
) -> None:
    """
    Validate that user has access to resource tenant
    
    Args:
        request: FastAPI request
        resource_tenant_id: Tenant ID of the resource being accessed
        
    Raises:
        HTTPException: If tenant mismatch
        
    Example:
        # In API route
        campaign = await get_campaign(campaign_id)
        validate_tenant_access(request, campaign.tenant_id)
    """
    if not hasattr(request.state, "tenant_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context not available",
        )
    
    if request.state.tenant_id != resource_tenant_id:
        logger.warning(
            f"Tenant mismatch: request={request.state.tenant_id}, "
            f"resource={resource_tenant_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",  # Don't leak existence
        )


class TenantIsolationError(Exception):
    """Raised when tenant isolation is violated"""
    pass
