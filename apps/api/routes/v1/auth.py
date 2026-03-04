"""
Authentication API Routes

Provides JWT login and API key management endpoints.

SQL Schema (create manually via migration if not exists):

    CREATE TABLE users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        username VARCHAR(255) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        tenant_id UUID NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE api_keys (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        key_hash TEXT UNIQUE NOT NULL,
        user_id UUID NOT NULL REFERENCES users(id),
        tenant_id UUID NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now()
    );

Endpoints:
    POST /auth/login      - Authenticate with username/password
    POST /auth/api-keys   - Generate new API key (requires Bearer auth)
"""

import hashlib
import secrets
from typing import Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.adapters.db.postgres import get_db_session
from apps.api.middleware.auth import create_access_token, get_current_user, get_current_tenant


router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Request / Response models ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    """Login credentials"""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    """JWT token response"""
    access_token: str
    token_type: str = "bearer"


class ApiKeyResponse(BaseModel):
    """API key response"""
    api_key: str = Field(..., description="Plain-text API key. Save this securely - it will not be shown again.")


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Authenticate user and return JWT access token.
    
    Validates username/password against the users table and returns
    a JWT token containing user_id and tenant_id.
    """
    # Hash the provided password
    password_hash = hashlib.sha256(request.password.encode()).hexdigest()
    
    # Query users table
    result = await session.execute(
        text(
            "SELECT id, tenant_id, is_active "
            "FROM users "
            "WHERE username = :username AND password_hash = :password_hash "
            "LIMIT 1"
        ),
        {"username": request.username, "password_hash": password_hash},
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    
    if not row.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )
    
    user_id = UUID(str(row.id))
    tenant_id = UUID(str(row.tenant_id))
    
    # Generate JWT token
    access_token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
    )
    
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
    )


@router.post("/api-keys", response_model=ApiKeyResponse)
async def create_api_key(
    user_id: UUID = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Generate a new API key for the authenticated user.
    
    Requires Bearer authentication (JWT).
    Returns the plain-text API key once - store it securely.
    The key is stored as a SHA-256 hash in the database.
    """
    # Generate cryptographically random 40-character key
    api_key = secrets.token_urlsafe(30)[:40]  # Generate enough bytes then truncate
    
    # Hash the key for storage
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    
    # Store in api_keys table
    await session.execute(
        text(
            "INSERT INTO api_keys (key_hash, user_id, tenant_id, is_active, created_at) "
            "VALUES (:key_hash, :user_id, :tenant_id, TRUE, now())"
        ),
        {
            "key_hash": key_hash,
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
        },
    )
    await session.commit()
    
    return ApiKeyResponse(api_key=api_key)
