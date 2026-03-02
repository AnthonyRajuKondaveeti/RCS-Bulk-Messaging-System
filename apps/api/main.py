"""
FastAPI Application

Main entry point for the RCS Platform API.

Features:
    - RESTful API with FastAPI
    - Automatic OpenAPI documentation
    - Middleware for auth, tenancy, rate limiting
    - CORS configuration
    - Lifespan events for startup/shutdown
    - Health checks
"""

from contextlib import asynccontextmanager
import logging
from typing import Optional

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
import uvicorn

import structlog
from structlog.contextvars import bind_contextvars

from apps.core.config import get_settings
from apps.adapters.db.postgres import init_database, close_database
from apps.core.observability.logging import configure_structlog


# ── Logging must be configured before any logger call ──────────────────────
# This replaces the old logging.basicConfig() which emitted plain-text lines
# and had no request_id / tenant_id context.
_settings_for_logging = get_settings()
configure_structlog(
    log_level=_settings_for_logging.observability.log_level,
    service="api",
    log_format=_settings_for_logging.observability.log_format,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager
    
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting RCS Platform API")
    
    try:
        # Initialize database
        await init_database()
        logger.info("Database initialized")
        
        yield
        
    finally:
        # Shutdown
        logger.info("Shutting down RCS Platform API")
        await close_database()


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application
    
    Returns:
        Configured FastAPI app
    """
    settings = get_settings()
    
    # Create app
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Enterprise RCS messaging platform with SMS fallback",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )
    
    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=settings.security.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Add custom middleware (order matters — Starlette applies LIFO!)
    # Execution order (request →): RequestSize → RequestID → RateLimit → Auth → Tenancy
    #
    # 0. Request size limit (absolute outermost guard — protects every path)
    from apps.api.middleware.request_size import RequestSizeLimitMiddleware
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_body_size=10 * 1024 * 1024,  # 10 MB
    )

    # 1. Request ID (first for tracing — assigns correlation ID to all logs)
    from apps.api.middleware.request_id import RequestIDMiddleware
    app.add_middleware(RequestIDMiddleware)

    # 2. Rate limiting (before auth to prevent auth abuse)
    if settings.rate_limit.enabled:
        from apps.api.middleware.rate_limit import RateLimitMiddleware
        app.add_middleware(
            RateLimitMiddleware,
            redis_url=settings.redis.url,
            default_limit=settings.rate_limit.default_limit,
        )


    # 3. Authentication (validates credentials)
    from apps.api.middleware.auth import AuthMiddleware
    app.add_middleware(AuthMiddleware)
    
    # 4. Tenancy (enforces multi-tenant isolation)
    from apps.api.middleware.tenancy import TenancyMiddleware
    app.add_middleware(TenancyMiddleware)
    
    logger.info("Middleware configured")
    
    # Exception handlers
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Global exception handler"""
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal server error",
                "detail": str(exc) if settings.debug else "An error occurred",
            },
        )
    
    # Health check endpoint
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
        }
    
    # Readiness check
    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        """
        Readiness check endpoint.

        FIX (GAP 10): Old implementation hardcoded "ok" for both database and
        queue without ever actually connecting to either.  Load balancers sent
        traffic to dead pods as a result.

        Now performs real connectivity probes:
          - Database: simple SELECT 1
          - RabbitMQ: checks connection is open
        Returns HTTP 503 if either check fails so the pod is removed from rotation.
        """
        checks = {}
        overall_ok = True

        # 1. Database probe
        try:
            from apps.adapters.db.postgres import get_database
            db = get_database()
            async with db.session() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.error("Readiness: database check failed: %s", exc)
            checks["database"] = f"error: {exc}"
            overall_ok = False

        # 2. RabbitMQ probe
        try:
            from apps.adapters.queue.rabbitmq import RabbitMQAdapter
            settings_obj = settings  # closure over create_app() local
            probe = RabbitMQAdapter(url=settings_obj.rabbitmq.url, prefetch_count=1)
            await probe.connect()
            await probe.close()
            checks["queue"] = "ok"
        except Exception as exc:
            logger.error("Readiness: queue check failed: %s", exc)
            checks["queue"] = f"error: {exc}"
            overall_ok = False

        response_status = "ready" if overall_ok else "degraded"
        http_status_code = 200 if overall_ok else 503

        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            status_code=http_status_code,
            content={
                "status": response_status,
                "checks": checks,
            },
        )
    
    # Metrics endpoint — protected by internal token
    # Two valid strategies:
    #   A. Bind a separate Prometheus server to 127.0.0.1 (best for Kubernetes)
    #   B. Require X-Metrics-Token header (used here — works for any deployment)
    #
    # Set METRICS_TOKEN in .env.prod (long random string, keep it secret).
    # Prometheus scrape config: scrape_configs > static_configs > params >
    #   headers: {X-Metrics-Token: ["<token>"]}
    if settings.observability.enable_metrics:
        from fastapi import Header, HTTPException
        from fastapi.responses import Response
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

        metrics_token = getattr(settings.observability, "metrics_token", None)

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint(
            x_metrics_token: Optional[str] = Header(default=None),
        ):
            """
            Prometheus metrics endpoint — protected by X-Metrics-Token.

            Set METRICS_TOKEN env var. Pass the same value in the
            Prometheus scrape config as a custom header.
            Leave METRICS_TOKEN unset only in local dev (disables auth).
            """
            if metrics_token and x_metrics_token != metrics_token:
                raise HTTPException(
                    status_code=403,
                    detail="Invalid metrics token",
                )
            return Response(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST,
            )
    
    # Register API routes
    from apps.api.routes.v1 import campaigns, webhooks, templates, audiences
    
    app.include_router(
        campaigns.router,
        prefix=f"{settings.api_prefix}/v1",
        tags=["Campaigns"],
    )
    
    app.include_router(
        webhooks.router,
        prefix=f"{settings.api_prefix}/v1",
        tags=["Webhooks"],
    )
    
    app.include_router(
        templates.router,
        prefix=f"{settings.api_prefix}/v1",
        tags=["Templates"],
    )
    
    app.include_router(
        audiences.router,
        prefix=f"{settings.api_prefix}/v1",
        tags=["Audiences"],
    )
    
    logger.info("API routes registered")
    
    logger.info(f"FastAPI app created (env={settings.environment})")
    
    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    """Run development server"""
    settings = get_settings()
    
    uvicorn.run(
        "apps.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.observability.log_level.lower(),
    )
