"""
Rate Limiting Middleware

Implements rate limiting per tenant and API endpoint.
Uses Redis for distributed rate limiting.

Features:
    - Per-tenant rate limits
    - Per-endpoint limits
    - Sliding window algorithm
    - Graceful degradation (continues if Redis unavailable)

Limits:
    - Default: 100 requests/minute per tenant
    - Custom limits per tenant (configurable)
    - Burst allowance
"""

import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as redis


logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware
    
    Implements sliding window rate limiting with Redis backend.
    Falls back gracefully if Redis is unavailable.
    """
    
    def __init__(
        self,
        app,
        redis_url: str,
        default_limit: int = 100,
        window: int = 60,
    ):
        """
        Initialize rate limiter
        
        Args:
            app: FastAPI app
            redis_url: Redis connection URL
            default_limit: Default requests per window
            window: Time window in seconds (default 60 = 1 minute)
        """
        super().__init__(app)
        self.redis_url = redis_url
        self.default_limit = default_limit
        self.window = window
        self.redis_client: Optional[redis.Redis] = None
        self._redis_available = True
    
    async def dispatch(self, request: Request, call_next):
        """Apply rate limiting"""
        
        # Skip for public endpoints
        if not hasattr(request.state, "authenticated"):
            return await call_next(request)
        
        # Get rate limit key
        tenant_id = request.state.tenant_id
        endpoint = self._get_endpoint_key(request)
        rate_limit_key = f"rate_limit:{tenant_id}:{endpoint}"
        
        # Check rate limit
        try:
            allowed, remaining, reset_time = await self._check_rate_limit(
                key=rate_limit_key,
                tenant_id=tenant_id,
            )
            
            if not allowed:
                logger.warning(
                    f"Rate limit exceeded for tenant {tenant_id} "
                    f"on endpoint {endpoint}"
                )
                return await self._rate_limit_response(
                    remaining=0,
                    reset_time=reset_time,
                )
            
            # Process request
            response = await call_next(request)
            
            # Add rate limit headers
            response.headers["X-RateLimit-Limit"] = str(self.default_limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_time)
            
            return response
            
        except Exception as e:
            logger.exception("Rate limiting error - allowing request")
            # Fail open - allow request if rate limiting fails
            return await call_next(request)
    
    async def _check_rate_limit(
        self,
        key: str,
        tenant_id: UUID,
    ) -> tuple[bool, int, int]:
        """
        Check if request is within rate limit
        
        Args:
            key: Rate limit key
            tenant_id: Tenant ID for custom limits
            
        Returns:
            (allowed, remaining, reset_time) tuple
        """
        # Get Redis client
        if not self.redis_client and self._redis_available:
            try:
                self.redis_client = await redis.from_url(self.redis_url)
            except Exception as e:
                logger.error(f"Redis connection failed: {e}")
                self._redis_available = False
        
        # If Redis unavailable, allow all requests
        if not self.redis_client:
            return True, self.default_limit, int(time.time() + self.window)
        
        try:
            # Get custom limit for tenant (if exists)
            limit = await self._get_tenant_limit(tenant_id)
            
            # Current timestamp
            now = time.time()
            window_start = now - self.window
            
            # Use sorted set for sliding window
            # Score is timestamp, value is request ID
            
            # Remove old entries outside window
            await self.redis_client.zremrangebyscore(
                key,
                "-inf",
                window_start,
            )
            
            # Count requests in current window
            count = await self.redis_client.zcard(key)
            
            # Check limit
            if count >= limit:
                # Get reset time (oldest entry + window)
                oldest = await self.redis_client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    reset_time = int(oldest[0][1] + self.window)
                else:
                    reset_time = int(now + self.window)
                
                return False, 0, reset_time
            
            # Add current request
            request_id = f"{now}:{id(key)}"
            await self.redis_client.zadd(key, {request_id: now})
            
            # Set expiry on key
            await self.redis_client.expire(key, self.window * 2)
            
            remaining = limit - count - 1
            reset_time = int(now + self.window)
            
            return True, remaining, reset_time
            
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            # Fail open
            return True, self.default_limit, int(time.time() + self.window)
    
    async def _get_tenant_limit(self, tenant_id: UUID) -> int:
        """
        Get custom rate limit for tenant
        
        TODO: Load from database/config
        For now, returns default
        """
        # In production, query tenant settings:
        # SELECT rate_limit FROM tenants WHERE id = tenant_id
        return self.default_limit
    
    def _get_endpoint_key(self, request: Request) -> str:
        """
        Get endpoint identifier for rate limiting
        
        Groups similar endpoints together.
        """
        path = request.url.path
        method = request.method
        
        # Group by API version and resource
        # e.g., POST /api/v1/campaigns/{id} -> POST:v1:campaigns
        parts = path.strip("/").split("/")
        
        if len(parts) >= 3 and parts[0] == "api":
            version = parts[1]  # v1
            resource = parts[2]  # campaigns
            return f"{method}:{version}:{resource}"
        
        return f"{method}:{path}"
    
    async def _rate_limit_response(
        self,
        remaining: int,
        reset_time: int,
    ):
        """Return rate limit exceeded response"""
        from starlette.responses import JSONResponse
        
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "Rate limit exceeded",
                "remaining": remaining,
                "reset_at": reset_time,
            },
            headers={
                "X-RateLimit-Limit": str(self.default_limit),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_time),
                "Retry-After": str(max(1, reset_time - int(time.time()))),
            },
        )
    
    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
