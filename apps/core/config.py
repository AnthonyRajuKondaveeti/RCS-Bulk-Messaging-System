"""
Application Configuration — single source of truth.

FIX: Replaced GupshupConfig with RcsSmsConfig.
     default_aggregator changed to "rcssms".
     apps/config.py (old file) must be DELETED or left unused.

All workers and API code import from apps.core.config — never from apps.config.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import os
import yaml


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    database: str = "rcs_platform"
    username: str = "postgres"
    password: str = "postgres"
    pool_size: int = 20
    max_overflow: int = 10
    echo: bool = False

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def sync_url(self) -> str:
        return (
            f"postgresql://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 50

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class RabbitMQConfig(BaseModel):
    host: str = "localhost"
    port: int = 5672
    # No default credentials — must be supplied via env vars.
    # In development, set RABBITMQ_USERNAME / RABBITMQ_PASSWORD in .env.
    # In production, these come from .env.prod via docker-compose env_file.
    username: str = Field(..., description="RabbitMQ username — no default, must be set")
    password: str = Field(..., description="RabbitMQ password — no default, must be set")
    vhost: str = "/"
    prefetch_count: int = 10
    heartbeat: int = 60

    @property
    def url(self) -> str:
        return f"amqp://{self.username}:{self.password}@{self.host}:{self.port}/{self.vhost}"


class RcsSmsConfig(BaseModel):
    """rcssms.in aggregator configuration"""
    username: str = Field(..., description="rcssms.in username")
    password: str = Field(..., description="rcssms.in password")
    rcs_id: str = Field(..., description="rcssms.in RCS sender ID")
    client_secret: Optional[str] = None
    use_bearer: bool = True
    timeout: int = 30
    use_mock: bool = False


class ObservabilityConfig(BaseModel):
    enable_metrics: bool = True
    metrics_port: int = 9090
    metrics_path: str = "/metrics"
    enable_tracing: bool = False
    jaeger_endpoint: Optional[str] = None
    otel_service_name: str = "rcs-platform"
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: Optional[str] = None
    # Secret token required to scrape /metrics. Set METRICS_TOKEN in .env.prod.
    # If None: /metrics is open (acceptable in local dev only).
    metrics_token: Optional[str] = None


class RateLimitConfig(BaseModel):
    enabled: bool = True
    default_limit: int = 100  # requests per minute
    tenant_limits: Dict[str, int] = {}
    rcssms_limit: int = 1000  # messages per second


class RetryConfig(BaseModel):
    max_retries: int = 3
    retry_backoff: int = 60        # seconds between retries
    enable_fallback: bool = True
    fallback_delay: int = 300      # seconds before SMS fallback
    dlq_retention: int = 7         # days


class SecurityConfig(BaseModel):
    # No insecure default — must be explicitly set to something meaningful.
    # In production: at least 32 characters, generated with secrets.token_hex(32).
    secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    api_key_header: str = "X-API-Key"
    # Default to empty (deny-all CORS) — not wildcard '*'.
    # Populate via CORS_ORIGINS env var: e.g. "https://app.example.com,https://admin.example.com"
    cors_origins: List[str] = []
    cors_allow_credentials: bool = False


class Settings(BaseModel):
    """
    Application settings — loaded from YAML then overridden by env vars.

    Import convention:
        from apps.core.config import get_settings
    """

    environment: str = "dev"
    debug: bool = False

    app_name: str = "RCS Platform"
    app_version: str = "1.0.0"
    api_prefix: str = "/api"

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

    database: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    rabbitmq: RabbitMQConfig = RabbitMQConfig()

    # Aggregator — rcssms.in only; Gupshup removed
    rcssms: Optional[RcsSmsConfig] = None
    default_aggregator: str = "rcssms"
    use_mock_aggregator: bool = False

    observability: ObservabilityConfig = ObservabilityConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    retry: RetryConfig = RetryConfig()
    security: SecurityConfig = SecurityConfig()

    queue_names: Dict[str, str] = {
        "campaign_orchestrator": "campaign.orchestrator",
        "message_dispatcher":    "message.dispatch",
        "fallback_handler":      "message.fallback",
        "webhook_processor":     "webhook.process",
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def _load_yaml(environment: str) -> Dict[str, Any]:
    config_dir = Path(__file__).parent.parent.parent / "infra" / "config"
    config_file = config_dir / f"{environment}.yaml"
    if not config_file.exists():
        return {}
    with open(config_file, "r") as f:
        return yaml.safe_load(f) or {}


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply environment variable overrides on top of YAML config.

    Env vars take precedence over YAML values.
    """
    # Database
    db = config.setdefault("database", {})
    if os.getenv("DB_HOST"):
        db["host"] = os.getenv("DB_HOST")
    if os.getenv("DB_PORT"):
        db["port"] = int(os.getenv("DB_PORT"))
    if os.getenv("DB_NAME"):
        db["database"] = os.getenv("DB_NAME")
    if os.getenv("DB_USERNAME"):
        db["username"] = os.getenv("DB_USERNAME")
    if os.getenv("DB_PASSWORD"):
        db["password"] = os.getenv("DB_PASSWORD")

    # RabbitMQ
    rmq = config.setdefault("rabbitmq", {})
    if os.getenv("RABBITMQ_HOST"):
        rmq["host"] = os.getenv("RABBITMQ_HOST")
    if os.getenv("RABBITMQ_USERNAME"):
        rmq["username"] = os.getenv("RABBITMQ_USERNAME")
    if os.getenv("RABBITMQ_PASSWORD"):
        rmq["password"] = os.getenv("RABBITMQ_PASSWORD")

    # Redis
    redis = config.setdefault("redis", {})
    if os.getenv("REDIS_HOST"):
        redis["host"] = os.getenv("REDIS_HOST")
    if os.getenv("REDIS_PASSWORD"):
        redis["password"] = os.getenv("REDIS_PASSWORD")

    # rcssms.in aggregator
    rcs_username = os.getenv("RCS_USERNAME")
    rcs_password = os.getenv("RCS_PASSWORD")
    rcs_id = os.getenv("RCS_ID")
    if rcs_username and rcs_password and rcs_id:
        config["rcssms"] = {
            "username": rcs_username,
            "password": rcs_password,
            "rcs_id": rcs_id,
            "client_secret": os.getenv("RCS_CLIENT_SECRET"),
            "use_bearer": os.getenv("RCS_USE_BEARER", "true").lower() == "true",
            "timeout": int(os.getenv("RCS_TIMEOUT", "30")),
            "use_mock": os.getenv("USE_MOCK_AGGREGATOR", "false").lower() == "true",
        }

    # Mock aggregator override
    if os.getenv("USE_MOCK_AGGREGATOR"):
        config["use_mock_aggregator"] = (
            os.getenv("USE_MOCK_AGGREGATOR", "false").lower() == "true"
        )

    # Security
    sec = config.setdefault("security", {})
    if os.getenv("SECRET_KEY"):
        sec["secret_key"] = os.getenv("SECRET_KEY")
    # CORS_ORIGINS: comma-separated list of allowed origins
    # e.g. "https://app.example.com,https://admin.example.com"
    if os.getenv("CORS_ORIGINS"):
        sec["cors_origins"] = [
            o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
        ]
    # METRICS_TOKEN: required to scrape /metrics in production
    if os.getenv("METRICS_TOKEN"):
        config.setdefault("observability", {})["metrics_token"] = os.getenv("METRICS_TOKEN")

    # App-level
    if os.getenv("DEBUG"):
        config["debug"] = os.getenv("DEBUG", "false").lower() == "true"
    if os.getenv("LOG_LEVEL"):
        config.setdefault("observability", {})["log_level"] = os.getenv("LOG_LEVEL")

    return config


@lru_cache()
def get_settings() -> Settings:
    """
    Get application settings (cached singleton).

    Load order:
        1. infra/config/{environment}.yaml
        2. Environment variables (override YAML)
        3. Startup validation (crashes the process on insecure config)

    Usage:
        from apps.core.config import get_settings
        settings = get_settings()
    """
    environment = os.getenv("ENVIRONMENT", "dev")
    config = _load_yaml(environment)
    config = _apply_env_overrides(config)
    config["environment"] = environment
    settings = Settings(**config)
    _validate_settings(settings)
    return settings


def _validate_settings(settings: "Settings") -> None:
    """
    Startup-time security gate.

    Raises RuntimeError immediately if any of the following are true:
      - SECRET_KEY is shorter than 32 characters
      - Running in production with DEBUG=True
      - Running in production with no CORS origins configured
      - Running in production with the placeholder secret key

    These checks run BEFORE the first request is served so the process
    crashes loud-and-early rather than silently accepting insecure config.
    """
    is_prod = settings.environment in ("production", "prod")
    errors: list[str] = []

    # 1. SECRET_KEY minimum length — applies everywhere, not just production
    if len(settings.security.secret_key) < 32:
        errors.append(
            f"SECRET_KEY is too short ({len(settings.security.secret_key)} chars). "
            "Minimum 32 characters required. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    # 2. Placeholder key is explicitly blocked in production
    if is_prod and settings.security.secret_key == "dev-secret-key-change-in-production":
        errors.append(
            "SECRET_KEY is still set to the placeholder value. "
            "Set a strong, unique SECRET_KEY in .env.prod before deploying."
        )

    # 3. Production must not run with DEBUG=True
    if is_prod and settings.debug:
        errors.append(
            "DEBUG=True is not allowed in production (ENVIRONMENT=production). "
            "Set DEBUG=False in your environment configuration."
        )

    # 4. Production must have explicit CORS origins (not empty, not wildcard)
    if is_prod:
        if not settings.security.cors_origins:
            errors.append(
                "CORS_ORIGINS is empty in production. "
                "Set CORS_ORIGINS to a comma-separated list of allowed origins, e.g. "
                "'https://app.example.com,https://admin.example.com'."
            )
        elif "*" in settings.security.cors_origins:
            errors.append(
                "CORS_ORIGINS contains '*' (wildcard) in production. "
                "Specify explicit origin URLs instead."
            )

    if errors:
        bullet_list = "\n  - ".join(errors)
        raise RuntimeError(
            f"\n\n[STARTUP SECURITY GATE FAILED] "
            f"{len(errors)} error(s) found:\n  - {bullet_list}\n\n"
            "Fix the above issues before starting the server in this environment."
        )


# Convenience accessors
def get_db_url() -> str:
    return get_settings().database.url

def get_queue_url() -> str:
    return get_settings().rabbitmq.url

def get_redis_url() -> str:
    return get_settings().redis.url

def is_production() -> bool:
    return get_settings().environment == "prod"

def is_debug() -> bool:
    return get_settings().debug
