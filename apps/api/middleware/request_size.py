"""
Request Size Limit Middleware

Phase 5: Security Hardening.

Protects the API from excessively large request bodies (e.g. 50 MB CSV uploads
that would exhaust memory, or raw HTTP flooding attempts).

Limits:
    Default:   10 MB  (covers JSON API payloads, inline contact lists, etc.)
    Override:  Content-Length header is checked first for a fast 413 before
               reading any body.  If Content-Length is missing (chunked transfer),
               the body is streamed and bytes counted — the connection is closed
               the moment the limit is exceeded.

Why middleware, not an upload endpoint limit:
    Route-level Body() limits only apply after the route is matched. A middleware
    limit fires before routing so it covers ALL paths including ones we forget
    to annotate, and returns a consistent 413 response.

Usage (registered in main.py BEFORE authentication middleware):
    from apps.api.middleware.request_size import RequestSizeLimitMiddleware
    app.add_middleware(RequestSizeLimitMiddleware, max_body_size=10 * 1024 * 1024)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

import structlog

logger = structlog.get_logger(__name__)

# Default: 10 MB
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024

# Paths that legitimately accept large uploads (e.g. CSV audience import)
# can be exempted here.  For now: none — all paths share the same limit.
_EXEMPT_PATHS: set[str] = set()


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Reject requests whose body exceeds `max_body_size` bytes.

    Two-pass check:
      1. Content-Length header present → fast reject before reading any bytes.
      2. No Content-Length (chunked) → consume the body up to the limit;
         reject immediately if exceeded.
    """

    def __init__(self, app: ASGIApp, max_body_size: int = DEFAULT_MAX_BODY_BYTES):
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Bodies are only relevant for methods that carry a payload
        if request.method in ("GET", "HEAD", "OPTIONS", "DELETE") or path in _EXEMPT_PATHS:
            return await call_next(request)

        # -- Fast path: Content-Length header present ----------------------
        content_length_header = request.headers.get("content-length")
        if content_length_header:
            try:
                claimed = int(content_length_header)
            except ValueError:
                return _too_large_response(
                    f"Malformed Content-Length header: {content_length_header!r}"
                )

            if claimed > self.max_body_size:
                logger.warning(
                    "request_body_too_large_by_header",
                    path=path,
                    claimed_bytes=claimed,
                    limit_bytes=self.max_body_size,
                )
                return _too_large_response(
                    f"Request body too large: declared {claimed} bytes, "
                    f"maximum allowed is {self.max_body_size} bytes."
                )

        # -- Slow path: chunked or no Content-Length; count as we stream ---
        # We read the raw body bytes up to the limit + 1.  If we get more
        # than the limit, reject.  This still buffers the whole body if it
        # is within bounds — that's the same as FastAPI's default behaviour.
        bytes_read = 0
        chunks: list[bytes] = []

        async for chunk in request.stream():
            bytes_read += len(chunk)
            chunks.append(chunk)
            if bytes_read > self.max_body_size:
                logger.warning(
                    "request_body_too_large_streaming",
                    path=path,
                    bytes_read=bytes_read,
                    limit_bytes=self.max_body_size,
                )
                return _too_large_response(
                    f"Request body too large: exceeded {self.max_body_size} bytes."
                )

        # Re-inject the buffered body into the request so downstream
        # middleware and route handlers can still read it as normal.
        async def receive():
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        request._receive = receive  # noqa: SLF001 — Starlette-internal hook

        return await call_next(request)


def _too_large_response(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"error": "Request Entity Too Large", "detail": detail},
        headers={"Connection": "close"},
    )
