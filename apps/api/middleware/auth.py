"""
Authentication Middleware

Handles API authentication using API keys and JWT tokens.

Supported Methods:
    - API Key (X-API-Key header)
    - JWT Bearer Token (Authorization header)

Usage:
    app.add_middleware(AuthMiddleware)
"""

import logging
from typing import Optional, Tuple
from uuid import UUID

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import jwt
from datetime import datetime, timedelta

from apps.core.config import get_settings


logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware
    
    Validates API keys or JWT tokens and adds user context to request state.
    
    Public endpoints (no auth required):
        - /health
        - /ready
        - /docs
        - /openapi.json
        - /api/v1/webhooks (validated separately)
    """
    
    # Endpoints that don't require authentication
    PUBLIC_PATHS = [
        "/health",
        "/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    ]
    
    def __init__(self, app):
        super().__init__(app)
        self.settings = get_settings()
    
    async def dispatch(self, request: Request, call_next):
        """Process authentication"""
        
        # Skip auth for public endpoints
        if self._is_public_path(request.url.path):
            return await call_next(request)
        
        # Skip auth for webhook endpoints (they have signature validation)
        if "/webhooks" in request.url.path:
            return await call_next(request)
        
        try:
            # Extract and validate credentials
            user_id, tenant_id = await self._authenticate_request(request)
            
            # Add to request state
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            request.state.authenticated = True
            
            # Process request
            response = await call_next(request)
            return response
            
        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content={"error": e.detail},
            )
        except Exception as e:
            logger.exception("Authentication error")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "Internal server error"},
            )
    
    async def _authenticate_request(
        self,
        request: Request,
    ) -> Tuple[UUID, UUID]:
        """
        Authenticate request using API key or JWT
        
        Returns:
            (user_id, tenant_id) tuple
            
        Raises:
            HTTPException: If authentication fails
        """
        # Try API key first
        api_key = request.headers.get(self.settings.security.api_key_header)
        if api_key:
            return await self._authenticate_api_key(api_key)
        
        # Try JWT token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            return await self._authenticate_jwt(token)
        
        # No credentials provided
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authentication credentials provided",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    async def _authenticate_api_key(
        self,
        api_key: str,
    ) -> Tuple[UUID, UUID]:
        """
        Authenticate using API key
        
        TODO: Implement actual API key validation against database
        For now, accepts any key matching pattern
        
        Args:
            api_key: API key from header
            
        Returns:
            (user_id, tenant_id) tuple
        """
        # TODO: Query database for API key
        # For now, mock implementation
        if len(api_key) < 32:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        
        # Mock user and tenant IDs
        # In production, these come from the API key record
        from uuid import uuid4
        user_id = uuid4()
        tenant_id = uuid4()
        
        logger.debug(f"Authenticated via API key: tenant={tenant_id}")
        
        return user_id, tenant_id
    
    async def _authenticate_jwt(
        self,
        token: str,
    ) -> Tuple[UUID, UUID]:
        """
        Authenticate using JWT token
        
        Args:
            token: JWT token
            
        Returns:
            (user_id, tenant_id) tuple
            
        Raises:
            HTTPException: If token is invalid
        """
        try:
            # Decode JWT
            payload = jwt.decode(
                token,
                self.settings.security.secret_key,
                algorithms=[self.settings.security.jwt_algorithm],
            )
            
            # Extract claims
            user_id = UUID(payload.get("sub"))
            tenant_id = UUID(payload.get("tenant_id"))
            
            # Check expiration
            exp = payload.get("exp")
            if exp and datetime.utcnow().timestamp() > exp:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired",
                )
            
            logger.debug(
                f"Authenticated via JWT: user={user_id}, tenant={tenant_id}"
            )
            
            return user_id, tenant_id
            
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
    
    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no auth required)"""
        return any(path.startswith(public) for public in self.PUBLIC_PATHS)


def create_access_token(
    user_id: UUID,
    tenant_id: UUID,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create JWT access token
    
    Args:
        user_id: User identifier
        tenant_id: Tenant identifier
        expires_delta: Token lifetime (default from settings)
        
    Returns:
        JWT token string
        
    Example:
        >>> token = create_access_token(user_id, tenant_id)
        >>> # Use in Authorization: Bearer {token}
    """
    settings = get_settings()
    
    if expires_delta is None:
        expires_delta = timedelta(
            minutes=settings.security.access_token_expire_minutes
        )
    
    expire = datetime.utcnow() + expires_delta
    
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "exp": expire.timestamp(),
        "iat": datetime.utcnow().timestamp(),
    }
    
    token = jwt.encode(
        payload,
        settings.security.secret_key,
        algorithm=settings.security.jwt_algorithm,
    )
    
    return token


# Dependency for FastAPI routes
async def get_current_user(request: Request) -> UUID:
    """
    FastAPI dependency to get current authenticated user
    
    Usage:
        @app.get("/profile")
        async def get_profile(user_id: UUID = Depends(get_current_user)):
            return {"user_id": user_id}
    """
    if not hasattr(request.state, "authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    
    return request.state.user_id


async def get_current_tenant(request: Request) -> UUID:
    """
    FastAPI dependency to get current tenant
    
    Usage:
        @app.get("/campaigns")
        async def list_campaigns(tenant_id: UUID = Depends(get_current_tenant)):
            return await campaign_service.list_campaigns(tenant_id)
    """
    if not hasattr(request.state, "authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    
    return request.state.tenant_id
