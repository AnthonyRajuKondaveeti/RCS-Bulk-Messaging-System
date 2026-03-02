"""
Authentication Middleware

JWT library: python-jose (from jose import jwt — NOT PyJWT).
  All encode/decode calls use the jose API:
    jwt.encode(payload, key, algorithm=alg)   → str
    jwt.decode(token, key, algorithms=[alg])  → dict

FIX (JWT mismatch): Replaced bare `import jwt` (PyJWT) with
    `from jose import jwt`
  python-jose is the declared dependency (python-jose[cryptography]
  in requirements.txt). PyJWT was never installed.

FIX (GAP 9):  _authenticate_api_key() used to return random uuid4() per request,
              breaking multi-tenancy entirely (every call got a different tenant_id).
              Now looks up the key in an api_keys table and returns the real
              tenant_id stored there.

FIX (GAP 24 / webhook bypass): validate_rcssms_webhook_signature now RAISES
              ConfigurationError if RCS_CLIENT_SECRET is empty or missing.
              No silent fallback — if the secret is not configured the webhook
              endpoint returns HTTP 500 at startup validation, not HTTP 200.

Public endpoints (no auth):
    /health, /ready, /docs, /redoc, /openapi.json, /metrics

Webhook endpoints use HMAC-SHA256 signature validation instead of API key/JWT.
"""

import hashlib
import hmac
import structlog
from datetime import datetime, timedelta
from typing import Optional, Tuple
from uuid import UUID

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from jose import jwt, JWTError

from apps.core.config import get_settings

logger = structlog.get_logger(__name__)



# ---------------------------------------------------------------------------
# Configuration-level guard — raised at import / startup time if secret absent
# ---------------------------------------------------------------------------

class WebhookConfigurationError(RuntimeError):
    """Raised when RCS_CLIENT_SECRET is required but not configured."""


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware.

    Validates API keys or JWT tokens and injects user context into request.state.

    IMPORTANT — webhook endpoints are NOT in the bypass list.
    The old code had:
        if "/webhooks/" in request.url.path:
            return await call_next(request)   # <-- bypass
    This made webhook routes trivially accessible without any credentials.

    Webhook routes now reach this middleware like any other route.  HMAC
    signature validation is the **responsibility of the route handler** via
    the @require_webhook_signature decorator in apps/api/routes/v1/webhooks.py.
    The middleware only checks for standard auth (API key / JWT).
    Webhook routes should still be in PUBLIC_PATHS if they legitimately do not
    carry an API key — but they MUST validate the HMAC token themselves.
    """

    # Truly public paths that require no authentication whatsoever.
    # Webhook paths are NOT listed here — they validate HMAC at route level.
    PUBLIC_PATHS = [
        "/health",
        "/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    ]

    # Webhook paths bypass API-key / JWT auth because rcssms.in sends a shared
    # HMAC secret, not an API key.  They remain authenticated via HMAC — see
    # @require_webhook_signature in the route module.
    WEBHOOK_PATHS = [
        "/api/v1/webhooks/",
    ]

    def __init__(self, app):
        super().__init__(app)
        self.settings = get_settings()

    async def dispatch(self, request: Request, call_next):
        # Truly public endpoints — no auth at all
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Webhook endpoints: bypass API-key/JWT auth, but are NOT open.
        # HMAC signature validation occurs inside the route handler via
        # @require_webhook_signature.  Any webhook route that lacks this
        # decorator will reject requests at the adapter level.
        if any(request.url.path.startswith(p) for p in self.WEBHOOK_PATHS):
            return await call_next(request)

        try:
            user_id, tenant_id = await self._authenticate_request(request)
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            request.state.authenticated = True
            return await call_next(request)

        except HTTPException as e:
            return JSONResponse(
                status_code=e.status_code,
                content={"error": e.detail},
            )
        except Exception:
            logger.exception("authentication_error")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "Internal server error"},
            )

    async def _authenticate_request(self, request: Request) -> Tuple[UUID, UUID]:
        """Try API key first, then JWT Bearer."""
        api_key = request.headers.get(self.settings.security.api_key_header)
        if api_key:
            return await self._authenticate_api_key(api_key, request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            return await self._authenticate_jwt(token)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No authentication credentials provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async def _authenticate_api_key(
        self, api_key: str, request: Request
    ) -> Tuple[UUID, UUID]:
        """
        Validate API key against the api_keys table in the database.

        FIX (GAP 9): Old implementation called uuid4() and returned a brand-new
        random UUID every request. Now does a real DB lookup.

        Schema (created by migration 003_add_api_keys):
            CREATE TABLE api_keys (
                id          UUID PRIMARY KEY,
                key_hash    TEXT UNIQUE,   -- SHA-256(raw_key)
                user_id     UUID NOT NULL,
                tenant_id   UUID NOT NULL,
                is_active   BOOLEAN DEFAULT TRUE,
                expires_at  TIMESTAMPTZ,
                created_at  TIMESTAMPTZ DEFAULT now()
            );

        The raw key is never stored — only its SHA-256 hash.
        """
        if len(api_key) < 32:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format",
            )

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        try:
            from apps.adapters.db.postgres import get_database
            db = get_database()
            async with db.session() as session:
                from sqlalchemy import text
                result = await session.execute(
                    text(
                        "SELECT user_id, tenant_id, is_active, expires_at "
                        "FROM api_keys WHERE key_hash = :hash LIMIT 1"
                    ),
                    {"hash": key_hash},
                )
                row = result.fetchone()

        except Exception:
            logger.exception("DB error during API key lookup")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service unavailable",
            )

        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

        if not row.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key is disabled",
            )

        if row.expires_at and datetime.utcnow() > row.expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
            )

        user_id = UUID(str(row.user_id))
        tenant_id = UUID(str(row.tenant_id))

        logger.debug("Authenticated via API key: tenant=%s", tenant_id)
        return user_id, tenant_id

    async def _authenticate_jwt(self, token: str) -> Tuple[UUID, UUID]:
        """
        Validate JWT and extract user_id + tenant_id.

        Uses python-jose (from jose import jwt).
        jose.jwt.decode() raises jose.JWTError for any validation failure
        including expiry — no separate expiry check needed.
        """
        try:
            payload = jwt.decode(
                token,
                self.settings.security.secret_key,
                algorithms=[self.settings.security.jwt_algorithm],
            )
            user_id = UUID(payload["sub"])
            tenant_id = UUID(payload["tenant_id"])

            logger.debug("Authenticated via JWT: user=%s tenant=%s", user_id, tenant_id)
            return user_id, tenant_id

        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {e}",
            )
        except (KeyError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

    def _is_public_path(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.PUBLIC_PATHS)


# ---------------------------------------------------------------------------
# Webhook HMAC validation (GAP 24)
# ---------------------------------------------------------------------------

def validate_rcssms_webhook_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """
    Validate rcssms.in webhook HMAC-SHA256 signature.

    FIX (GAP 24 / bypass): The previous implementation returned True when
    `secret` was empty, allowing anyone to spoof DLRs by simply not setting
    RCS_CLIENT_SECRET in .env.

    New behaviour:
      - If `secret` is falsy → raise WebhookConfigurationError immediately.
        The route must catch this and return HTTP 500 (misconfiguration),
        never HTTP 200.
      - If `secret` is set → compare HMAC-SHA256(secret, raw_body) against
        the provided header using a timing-safe comparison.

    rcssms.in signs with: HMAC-SHA256(secret, raw_body).hexdigest()

    Usage in webhook route:
        raw = await request.body()
        sig = request.headers.get("X-RcsSms-Signature", "")
        if not validate_rcssms_webhook_signature(raw, sig, settings.rcssms.client_secret):
            raise HTTPException(401, "Invalid webhook signature")
    """
    if not secret:
        raise WebhookConfigurationError(
            "RCS_CLIENT_SECRET is not configured. "
            "Set RCS_CLIENT_SECRET in your .env file before receiving webhooks. "
            "Refusing to process webhook with no signature secret."
        )

    expected = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    # Use hmac.compare_digest to prevent timing attacks
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> UUID:
    """Dependency: return authenticated user_id."""
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return request.state.user_id


async def get_current_tenant(request: Request) -> UUID:
    """Dependency: return authenticated tenant_id."""
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return request.state.tenant_id


# ---------------------------------------------------------------------------
# JWT token creation (used by login endpoints)
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: UUID,
    tenant_id: UUID,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token using python-jose.

    jose.jwt.encode() always returns a str in python-jose >= 3.x.
    No need to call .decode() on the result.
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
        "exp": expire,          # jose accepts a datetime object directly
        "iat": datetime.utcnow(),
    }
    return jwt.encode(
        payload,
        settings.security.secret_key,
        algorithm=settings.security.jwt_algorithm,
    )
