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
from pydantic import BaseModel, Field, PostgresDsn, field_validator
import yaml
import os
from dotenv import load_dotenv

# Load .env file explicitly
env_path = os.path.join(os.getcwd(), '.env')
loaded = load_dotenv(env_path, override=False)
print(f"DEBUG: load_dotenv from {env_path} returned {loaded} (OVERRIDE=TRUE)")
print(f"DEBUG: OS DB_NAME={os.getenv('DB_NAME')}")
print(f"DEBUG: OS DB_PASSWORD length={len(os.getenv('DB_PASSWORD', ''))}")


class DatabaseConfig(BaseModel):
    """Database configuration"""
    host: str = Field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    port: int = Field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    database: str = Field(default_factory=lambda: os.getenv("DB_NAME", "rcs_platform"))
    username: str = Field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    password: str = Field(default_factory=lambda: os.getenv("DB_PASSWORD", "postgres"))
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
    host: str = Field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    port: int = Field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    db: int = 0
    password: Optional[str] = Field(default_factory=lambda: os.getenv("REDIS_PASSWORD"))
    max_connections: int = 50
    
    @property
    def url(self) -> str:
        """Construct Redis URL"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class RabbitMQConfig(BaseModel):
    """RabbitMQ configuration"""
    host: str = Field(default_factory=lambda: os.getenv("RABBITMQ_HOST", "localhost"))
    port: int = Field(default_factory=lambda: int(os.getenv("RABBITMQ_PORT", "5672")))
    username: str = Field(default_factory=lambda: os.getenv("RABBITMQ_USER", "guest"))
    password: str = Field(default_factory=lambda: os.getenv("RABBITMQ_PASSWORD", "guest"))
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
    use_mock: bool = Field(False, env="USE_MOCK_AGGREGATOR")


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
    jaeger_endpoint: Optional[str] = Field(default_factory=lambda: os.getenv("JAEGER_ENDPOINT"))
    otel_service_name: str = "rcs-platform"
    
    # Logging
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_format: str = Field(default_factory=lambda: os.getenv("LOG_FORMAT", "json"))
    log_file: Optional[str] = None


class RateLimitConfig(BaseModel):
    """Rate limiting configuration"""
    enabled: bool = Field(default_factory=lambda: os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true")
    default_limit: int = Field(default_factory=lambda: int(os.getenv("DEFAULT_RATE_LIMIT", "100")))
    tenant_limits: Dict[str, int] = {}
    
    # Per-aggregator limits
    gupshup_limit: int = Field(default_factory=lambda: int(os.getenv("GUPSHUP_RATE_LIMIT", "1000")))
    route_limit: int = 500


class RetryConfig(BaseModel):
    """Retry and fallback configuration"""
    max_retries: int = Field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    retry_backoff: int = Field(default_factory=lambda: int(os.getenv("RETRY_BACKOFF", "60")))
    enable_fallback: bool = Field(default_factory=lambda: os.getenv("ENABLE_FALLBACK", "true").lower() == "true")
    fallback_delay: int = Field(default_factory=lambda: int(os.getenv("FALLBACK_DELAY", "300")))
    dlq_retention: int = 7  # days


class SecurityConfig(BaseModel):
    """Security configuration"""
    secret_key: str = Field(default_factory=lambda: os.getenv("SECRET_KEY", "change-me"))
    jwt_algorithm: str = Field(default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256"))
    access_token_expire_minutes: int = Field(default_factory=lambda: int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")))
    
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
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "dev"))
    debug: bool = Field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    
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
    use_mock_aggregator: bool = Field(default_factory=lambda: os.getenv("USE_MOCK_AGGREGATOR", "false").lower() == "true")
    
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
    
    # Removed class Config for model_config
    
    @field_validator("environment")
    @classmethod
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
    print(f"DEBUG: Loading config from {config_file}, exists={config_file.exists()}")
    
    if not config_file.exists():
        return {}
    
    try:
        # Try UTF-8 first
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # Fallback to UTF-16 (common for Windows-generated files)
        with open(config_file, "r", encoding="utf-16") as f:
            content = f.read()
            
    parsed = yaml.safe_load(content) or {}
    return parsed


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
    
    # Create settings
    settings = Settings(**config)
    
    # Manually override with env vars
    if os.getenv("DB_HOST"): settings.database.host = os.getenv("DB_HOST")
    if os.getenv("DB_PORT"): settings.database.port = int(os.getenv("DB_PORT"))
    if os.getenv("DB_NAME"): settings.database.database = os.getenv("DB_NAME")
    if os.getenv("DB_USER"): settings.database.username = os.getenv("DB_USER")
    if os.getenv("DB_PASSWORD"): settings.database.password = os.getenv("DB_PASSWORD")
    
    if os.getenv("RABBITMQ_HOST"): settings.rabbitmq.host = os.getenv("RABBITMQ_HOST")
    if os.getenv("RABBITMQ_PORT"): settings.rabbitmq.port = int(os.getenv("RABBITMQ_PORT"))
    if os.getenv("RABBITMQ_USER"): settings.rabbitmq.username = os.getenv("RABBITMQ_USER")
    if os.getenv("RABBITMQ_PASSWORD"): settings.rabbitmq.password = os.getenv("RABBITMQ_PASSWORD")
    
    if os.getenv("REDIS_HOST"): settings.redis.host = os.getenv("REDIS_HOST")
    if os.getenv("REDIS_PORT"): settings.redis.port = int(os.getenv("REDIS_PORT"))
    if os.getenv("REDIS_PASSWORD"): settings.redis.password = os.getenv("REDIS_PASSWORD")
    
    if os.getenv("USE_MOCK_AGGREGATOR"): 
        settings.use_mock_aggregator = os.getenv("USE_MOCK_AGGREGATOR").lower() == "true"
        
    return settings


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
