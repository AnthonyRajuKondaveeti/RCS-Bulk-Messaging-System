"""
Request ID Middleware

Phase 4 update: the old `_logging_context` method was a no-op placeholder —
it created an inner class with empty `__enter__`/`__exit__` methods and
never actually bound request_id to anything.

Now uses structlog.contextvars to propagate request_id (and tenant_id once
the auth middleware has resolved it) through the entire request lifecycle.
Every log line emitted inside an asyncio Task that started from this
middleware will automatically include:

    "request_id": "<uuid>",
    "tenant_id":  "<uuid>",   ← bound by TenancyMiddleware later
    "service":    "api"       ← bound at startup by configure_structlog()

Headers:
    X-Request-ID: Unique request identifier (accept from client or generate)
    X-Correlation-ID: Same value — for Jaeger / distributed tracing correlation
"""

import uuid
from typing import Optional

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
)

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


logger = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Request ID middleware.

    For every incoming request:
      1. Accept X-Request-ID if valid UUID, otherwise generate a new one.
      2. Store in request.state.request_id and request.state.correlation_id.
      3. Bind to structlog contextvars so ALL log lines for this request
         automatically carry request_id — no manual `extra={}` needed.
      4. Add X-Request-ID and X-Correlation-ID to the response headers.
      5. Clear contextvars after the response is sent so the asyncio Task
         context does not leak into the next request handled by this worker.
    """

    async def dispatch(self, request: Request, call_next):
        # 1. Resolve request ID
        request_id = _get_or_generate_request_id(request)

        # 2. Store on request state (available to route handlers)
        request.state.request_id = request_id
        request.state.correlation_id = request_id

        # 3. Bind to structlog contextvars — from this point every log call
        #    in this asyncio Task carries request_id automatically.
        #    clear_contextvars() first ensures no context leaks from a
        #    previous request that shared this worker Task.
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )

        # 4. Execute the rest of the middleware + route handler
        response = await call_next(request)

        # TenancyMiddleware / AuthMiddleware will have bound tenant_id by now;
        # pull it back out for the completion log.
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            bind_contextvars(tenant_id=str(tenant_id))

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
        )

        # 5. Add correlation headers to response
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = request_id

        # 6. Clear context so next request starts clean
        clear_contextvars()

        return response


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_or_generate_request_id(request: Request) -> str:
    """Accept a client-supplied X-Request-ID (must be valid UUID), or mint one."""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming:
        try:
            uuid.UUID(incoming)
            return incoming
        except ValueError:
            logger.warning("invalid_request_id_header", received=incoming)
    return str(uuid.uuid4())


def get_request_id(request: Request) -> Optional[str]:
    """FastAPI dependency / utility: return the request ID from request.state."""
    return getattr(request.state, "request_id", None)


def get_correlation_id(request: Request) -> Optional[str]:
    """Return correlation ID (same as request_id — for Jaeger propagation)."""
    return getattr(request.state, "correlation_id", None)
