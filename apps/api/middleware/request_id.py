"""
Request ID Middleware

Generates and propagates correlation IDs for distributed tracing.

Features:
    - Generate unique request ID
    - Propagate through all services
    - Log with every operation
    - Return in response headers

Headers:
    - X-Request-ID: Unique request identifier
    - X-Correlation-ID: For distributed tracing
"""

import logging
import uuid
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Request ID middleware
    
    Generates correlation IDs for request tracing across services.
    
    Flow:
        1. Check for existing X-Request-ID header
        2. Generate new UUID if not present
        3. Add to request state
        4. Add to response headers
        5. Log with all operations
    """
    
    REQUEST_ID_HEADER = "X-Request-ID"
    CORRELATION_ID_HEADER = "X-Correlation-ID"
    
    async def dispatch(self, request: Request, call_next):
        """Add request ID to request"""
        
        # Get or generate request ID
        request_id = self._get_or_generate_request_id(request)
        
        # Add to request state
        request.state.request_id = request_id
        request.state.correlation_id = request_id
        
        # Add to logging context
        # This makes request_id available in all log messages
        with self._logging_context(request_id):
            logger.info(
                f"Request started: {request.method} {request.url.path}",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "client": request.client.host if request.client else None,
                },
            )
            
            # Process request
            response = await call_next(request)
            
            # Add headers to response
            response.headers[self.REQUEST_ID_HEADER] = request_id
            response.headers[self.CORRELATION_ID_HEADER] = request_id
            
            logger.info(
                f"Request completed: {request.method} {request.url.path} "
                f"(status={response.status_code})",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                },
            )
            
            return response
    
    def _get_or_generate_request_id(self, request: Request) -> str:
        """
        Get request ID from header or generate new one
        
        Args:
            request: FastAPI request
            
        Returns:
            Request ID string
        """
        # Check if client provided request ID
        request_id = request.headers.get(self.REQUEST_ID_HEADER)
        
        if request_id:
            # Validate format
            try:
                uuid.UUID(request_id)
                return request_id
            except ValueError:
                logger.warning(f"Invalid request ID format: {request_id}")
        
        # Generate new UUID
        return str(uuid.uuid4())
    
    def _logging_context(self, request_id: str):
        """
        Context manager for adding request_id to logs
        
        This would integrate with structured logging to automatically
        include request_id in all log messages within this context.
        """
        # This is a placeholder - actual implementation would use
        # contextvars or a logging filter
        
        class LoggingContext:
            def __enter__(self):
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        
        return LoggingContext()


def get_request_id(request: Request) -> Optional[str]:
    """
    Get request ID from request state
    
    Usage:
        @app.get("/endpoint")
        async def endpoint(request: Request):
            request_id = get_request_id(request)
            logger.info(f"Processing {request_id}")
    """
    return getattr(request.state, "request_id", None)


def get_correlation_id(request: Request) -> Optional[str]:
    """Get correlation ID for distributed tracing"""
    return getattr(request.state, "correlation_id", None)
