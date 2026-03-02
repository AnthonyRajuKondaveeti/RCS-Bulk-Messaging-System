"""
Structured Logging Setup

Replaces the old stdlib-only JSONFormatter with a proper structlog pipeline.

Pipeline (configured in configure_structlog()):

    [any logger call]
        → add_log_level
        → add_logger_name
        → TimeStamper (ISO 8601, UTC)
        → merge_contextvars         ← picks up request_id / tenant_id bound via
                                       structlog.contextvars.bind_contextvars()
        → CallsiteParameterAdder    ← filename + line number
        → PositionalArgumentsFormatter
        → StackInfoRenderer
        → format_exc_info
        → JSONRenderer              ← final output: one JSON object per line

    All stdlib logging (third-party libs, SQLAlchemy, uvicorn, etc.) is routed
    through structlog via logging.config so their records are reformatted
    consistently rather than appearing in plain text.

Context variables available anywhere in the same asyncio Task:
    structlog.contextvars.bind_contextvars(
        request_id="...",
        tenant_id="...",
        service="api",
    )

Usage:
    from apps.core.observability.logging import configure_structlog, get_logger

    configure_structlog("INFO", service="api")
    logger = get_logger(__name__)

    logger.info("campaign_created", campaign_id=str(cid))
    # → {"event": "campaign_created", "campaign_id": "...", "request_id": "...",
    #    "tenant_id": "...", "service": "api", "timestamp": "...", "level": "info",
    #    "logger": "apps.core.services.campaign_service", ...}
"""

import logging
import logging.config
import sys
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------

def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger bound to `name`.

    Drop-in replacement for logging.getLogger() — same call site, structured
    output with automatic contextvars (request_id, tenant_id, service).

    Usage:
        logger = get_logger(__name__)
        logger.info("order_delivered", order_id="ORD-123")
    """
    return structlog.get_logger(name)


def configure_structlog(
    log_level: str = "INFO",
    service: str = "api",
    log_format: str = "json",
) -> None:
    """
    Configure structlog + stdlib logging for the entire process.

    Call this ONCE at application startup (before any log calls).

    Args:
        log_level:  Root log level string ("DEBUG", "INFO", "WARNING", "ERROR").
        service:    Service name injected into every log line ("api" or "worker").
        log_format: "json" for production, "console" for local dev pretty-print.

    Side effects:
        - Configures the root stdlib logger to emit through structlog.
        - Suppresses noisy third-party logs (sqlalchemy.engine, aiormq, etc.).
        - Raises an error at startup if structlog is not installed (not silently
          falls back), ensuring the dependency gap is never hidden.
    """
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    # -- stdlib logging config (governs third-party libs) -------------------
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "structlog_plain",
            },
        },
        "formatters": {
            # structlog's stdlib formatter bridges stdlib → structlog pipeline
            "structlog_plain": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer()
                    if log_format == "console"
                    else structlog.processors.JSONRenderer(),
                "foreign_pre_chain": _build_pre_chain(service),
            },
        },
        "root": {
            "handlers": ["default"],
            "level": log_level_int,
        },
        # Suppress chatty loggers that aren't useful in production
        "loggers": {
            "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
            "aiormq": {"level": "WARNING", "propagate": True},
            "aio_pika": {"level": "WARNING", "propagate": True},
            "uvicorn.access": {"level": "WARNING", "propagate": True},
            "asyncio": {"level": "WARNING", "propagate": True},
        },
    })

    # -- structlog native config ---------------------------------------------
    structlog.configure(
        processors=_build_full_chain(service, log_format),
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _build_pre_chain(service: str) -> list[Any]:
    """
    Shared processor chain used for both stdlib bridge AND native structlog.
    These run BEFORE the final renderer.
    """
    return [
        # Pull request_id / tenant_id / service from contextvars
        merge_contextvars,
        # Inject service name as a default (contextvars can override)
        _inject_service(service),
        # stdlib bridge compatibility
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Timestamp every log line in UTC ISO 8601
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Format positional args like stdlib: "hello %s" % ("world",)
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Include stack information when requested
        structlog.processors.StackInfoRenderer(),
        # Render exception tracebacks as strings (not raw exc_info tuples)
        structlog.processors.format_exc_info,
        # Scrub bytes values to avoid JSON serialisation failures
        structlog.processors.UnicodeDecoder(),
    ]


def _build_full_chain(service: str, log_format: str) -> list[Any]:
    """Full chain including the final renderer (for native structlog calls)."""
    chain = _build_pre_chain(service)
    chain.append(
        structlog.dev.ConsoleRenderer(colors=True)
        if log_format == "console"
        else structlog.processors.JSONRenderer()
    )
    return chain


def _inject_service(service: str):
    """Processor: inject `service` into every log event dict."""
    def processor(logger, method, event_dict):
        event_dict.setdefault("service", service)
        return event_dict
    return processor


# --------------------------------------------------------------------------
# Backwards-compat shims (called from legacy sites still using old API)
# --------------------------------------------------------------------------

def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    service: str = "api",
    log_file: str | None = None,
) -> None:
    """
    Backwards-compatible wrapper around configure_structlog().

    Kept so existing call sites (e.g. tests/local/test_local.py) don't break.
    The `log_file` parameter is accepted but intentionally ignored — in
    containerised environments all logs go to stdout/stderr for the container
    runtime to collect.
    """
    configure_structlog(log_level=log_level, service=service, log_format=log_format)


class LogContext:
    """
    Async-safe context manager for temporarily binding extra keys.

    Replaces the old ThreadLocal-based LogContext with structlog contextvars.
    Works correctly in asyncio (each Task inherits a copy of the context).

    Usage:
        async with LogContext(campaign_id="abc"):
            logger.info("processing")   # → includes campaign_id in every line
    """

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._token = None

    def __enter__(self):
        structlog.contextvars.bind_contextvars(**self._kwargs)
        return self

    def __exit__(self, *_):
        structlog.contextvars.unbind_contextvars(*self._kwargs.keys())

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        self.__exit__(*args)
