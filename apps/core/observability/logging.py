"""
Structured Logging

Provides structured JSON logging with correlation IDs.

Features:
    - JSON formatted logs
    - Correlation ID tracking
    - Context managers for scoped logging
    - Request/response logging
    - Error tracking

Usage:
    from apps.core.observability.logging import get_logger
    
    logger = get_logger(__name__)
    logger.info("Campaign created", campaign_id=str(campaign_id))
"""

import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter
    
    Formats log records as JSON for structured logging.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id
        
        if hasattr(record, 'tenant_id'):
            log_data['tenant_id'] = str(record.tenant_id)
        
        if hasattr(record, 'user_id'):
            log_data['user_id'] = str(record.user_id)
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add any custom fields from extra parameter
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename',
                          'funcName', 'levelname', 'levelno', 'lineno',
                          'module', 'msecs', 'pathname', 'process',
                          'processName', 'relativeCreated', 'thread',
                          'threadName', 'exc_info', 'exc_text', 'stack_info',
                          'request_id', 'tenant_id', 'user_id']:
                # Handle UUID types
                if isinstance(value, UUID):
                    log_data[key] = str(value)
                elif isinstance(value, (str, int, float, bool, type(None))):
                    log_data[key] = value
                else:
                    log_data[key] = str(value)
        
        return json.dumps(log_data)


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    log_file: Optional[str] = None,
) -> None:
    """
    Setup application logging
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Format (json or text)
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    
    # Set formatter
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Add file handler if configured
    if log_file:
        from pathlib import Path
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.FileHandler(str(log_path))
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")


def get_logger(name: str) -> logging.Logger:
    """
    Get logger instance
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
        
    Usage:
        logger = get_logger(__name__)
        logger.info("Message", extra={"key": "value"})
    """
    return logging.getLogger(name)


class LogContext:
    """
    Context manager for scoped logging with additional context
    
    Usage:
        with LogContext(request_id="abc123", tenant_id=uuid):
            logger.info("Inside context")  # Will include request_id and tenant_id
    """
    
    def __init__(self, **kwargs):
        self.context = kwargs
        self.old_factory = None
    
    def __enter__(self):
        old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            for key, value in self.context.items():
                setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        self.old_factory = old_factory
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_factory:
            logging.setLogRecordFactory(self.old_factory)


def log_function_call(func):
    """
    Decorator to log function calls
    
    Usage:
        @log_function_call
        async def my_function(arg1, arg2):
            pass
    """
    import functools
    
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        logger.debug(
            f"Calling {func.__name__}",
            function=func.__name__,
            args=len(args),
            kwargs=list(kwargs.keys()),
        )
        
        try:
            result = await func(*args, **kwargs)
            logger.debug(f"Completed {func.__name__}", function=func.__name__)
            return result
        except Exception as e:
            logger.error(
                f"Error in {func.__name__}: {e}",
                function=func.__name__,
                error=str(e),
            )
            raise
    
    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        logger.debug(
            f"Calling {func.__name__}",
            function=func.__name__,
            args=len(args),
            kwargs=list(kwargs.keys()),
        )
        
        try:
            result = func(*args, **kwargs)
            logger.debug(f"Completed {func.__name__}", function=func.__name__)
            return result
        except Exception as e:
            logger.error(
                f"Error in {func.__name__}: {e}",
                function=func.__name__,
                error=str(e),
            )
            raise
    
    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
