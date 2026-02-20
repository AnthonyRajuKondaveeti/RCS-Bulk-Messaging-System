"""
Application Configuration

Centralized configuration management with environment-based settings.
Supports dev, staging, and production environments.

Configuration Sources:
    1. YAML files (dev.yaml, staging.yaml, prod.yaml)
    2. Environment variables (override YAML)
    3. Secrets management (AWS Secrets Manager, HashiCorp Vault)

Usage:
    >>> from apps.core.config import get_settings
    >>> settings = get_settings()
    >>> print(settings.database.url)
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, PostgresDsn, validator
import yaml
import os


class DatabaseConfig(BaseModel):
    """Database configuration"""
    host: str = "localhost"
    port: int = 5432
    database: str = "rcs_platform"
    username: str = "postgres"
    password: str = Field(..., env="DB_PASSWORD")
    pool_size: int = 20
    max_overflow: int = 10
    echo: bool = False
    
    @property
    def url(self) -> str:
        """Construct database URL"""
        return (
            f"postgresql+asyncpg://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )
    
    @property
    def sync_url(self) -> str:
        """Construct sync database URL for migrations"""
        return (
            f"postgresql://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class RedisConfig(BaseModel):
    """Redis configuration for caching and rate limiting"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 50
    
    @property
    def url(self) -> str:
        """Construct Redis URL"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class RabbitMQConfig(BaseModel):
    """RabbitMQ configuration"""
    host: str = "localhost"
    port: int = 5672
    username: str = "guest"
    password: str = Field("guest", env="RABBITMQ_PASSWORD")
    vhost: str = "/"
    prefetch_count: int = 10
    heartbeat: int = 60
    
    @property
    def url(self) -> str:
        """Construct RabbitMQ URL"""
        return f"amqp://{self.username}:{self.password}@{self.host}:{self.port}/{self.vhost}"


class GupshupConfig(BaseModel):
    """Gupshup aggregator configuration"""
    api_key: str = Field(..., env="GUPSHUP_API_KEY")
    app_name: str = Field(..., env="GUPSHUP_APP_NAME")
    base_url: str = "https://api.gupshup.io/wa/api/v1"
    webhook_secret: str = Field(..., env="GUPSHUP_WEBHOOK_SECRET")
    timeout: int = 30
    max_retries: int = 3


class RouteConfig(BaseModel):
    """Route Mobile aggregator configuration"""
    api_key: str = Field(..., env="ROUTE_API_KEY")
    sender_id: str = Field(..., env="ROUTE_SENDER_ID")
    base_url: str = "https://api.route.com/v1"
    webhook_secret: str = Field(..., env="ROUTE_WEBHOOK_SECRET")
    timeout: int = 30


class ObservabilityConfig(BaseModel):
    """Observability configuration"""
    # Prometheus
    enable_metrics: bool = True
    metrics_port: int = 9090
    metrics_path: str = "/metrics"
    
    # OpenTelemetry
    enable_tracing: bool = True
    jaeger_endpoint: Optional[str] = None
    otel_service_name: str = "rcs-platform"
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or text
    log_file: Optional[str] = None


class RateLimitConfig(BaseModel):
    """Rate limiting configuration"""
    enabled: bool = True
    default_limit: int = 100  # requests per minute
    tenant_limits: Dict[str, int] = {}
    
    # Per-aggregator limits
    gupshup_limit: int = 1000  # messages per second
    route_limit: int = 500


class RetryConfig(BaseModel):
    """Retry and fallback configuration"""
    max_retries: int = 3
    retry_backoff: int = 60  # seconds
    enable_fallback: bool = True
    fallback_delay: int = 300  # 5 minutes before SMS fallback
    dlq_retention: int = 7  # days


class SecurityConfig(BaseModel):
    """Security configuration"""
    secret_key: str = Field(..., env="SECRET_KEY")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # API Key authentication
    api_key_header: str = "X-API-Key"
    
    # CORS
    cors_origins: List[str] = ["*"]
    cors_allow_credentials: bool = True


class Settings(BaseModel):
    """
    Application settings
    
    Loads configuration from YAML file and environment variables.
    Environment variables override YAML values.
    """
    
    # Environment
    environment: str = Field("dev", env="ENVIRONMENT")
    debug: bool = Field(False, env="DEBUG")
    
    # Application
    app_name: str = "RCS Platform"
    app_version: str = "1.0.0"
    api_prefix: str = "/api"
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    
    # Components
    database: DatabaseConfig
    redis: RedisConfig
    rabbitmq: RabbitMQConfig
    
    # Aggregators
    gupshup: Optional[GupshupConfig] = None
    route: Optional[RouteConfig] = None
    default_aggregator: str = "gupshup"
    
    # Features
    observability: ObservabilityConfig = ObservabilityConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    retry: RetryConfig = RetryConfig()
    security: SecurityConfig
    
    # Queue names
    queue_names: Dict[str, str] = {
        "campaign_orchestrator": "campaign.orchestrator",
        "message_dispatcher": "message.dispatch",
        "fallback_handler": "message.fallback",
        "webhook_processor": "webhook.process",
    }
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    @validator("environment")
    def validate_environment(cls, v):
        """Validate environment value"""
        allowed = ["dev", "staging", "prod"]
        if v not in allowed:
            raise ValueError(f"Environment must be one of {allowed}")
        return v


def load_config_file(environment: str) -> Dict[str, Any]:
    """
    Load configuration from YAML file
    
    Args:
        environment: Environment name (dev, staging, prod)
        
    Returns:
        Configuration dictionary
    """
    config_dir = Path(__file__).parent.parent.parent / "infra" / "config"
    config_file = config_dir / f"{environment}.yaml"
    
    if not config_file.exists():
        return {}
    
    with open(config_file, "r") as f:
        return yaml.safe_load(f) or {}


@lru_cache()
def get_settings() -> Settings:
    """
    Get application settings (cached singleton)
    
    Returns:
        Settings instance
        
    Example:
        >>> settings = get_settings()
        >>> db_url = settings.database.url
    """
    environment = os.getenv("ENVIRONMENT", "dev")
    
    # Load YAML config
    config = load_config_file(environment)
    
    # Create settings (env vars override YAML)
    return Settings(**config)


# Convenience accessors
def get_db_url() -> str:
    """Get database URL"""
    return get_settings().database.url


def get_queue_url() -> str:
    """Get message queue URL"""
    return get_settings().rabbitmq.url


def get_redis_url() -> str:
    """Get Redis URL"""
    return get_settings().redis.url


def is_production() -> bool:
    """Check if running in production"""
    return get_settings().environment == "prod"


def is_debug() -> bool:
    """Check if debug mode is enabled"""
    return get_settings().debug
