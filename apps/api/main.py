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

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
import uvicorn

from apps.core.config import get_settings
from apps.adapters.db.postgres import init_database, close_database


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    
    # Add custom middleware (order matters!)
    # 1. Request ID (first - for tracing all requests)
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
        """Readiness check endpoint"""
        # TODO: Check database, queue, etc.
        return {
            "status": "ready",
            "checks": {
                "database": "ok",
                "queue": "ok",
            }
        }
    
    # Metrics endpoint
    if settings.observability.enable_metrics:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)
    
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
