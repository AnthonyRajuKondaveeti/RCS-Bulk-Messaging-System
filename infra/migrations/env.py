"""
Alembic Environment Configuration

Configures Alembic for async SQLAlchemy migrations.
Supports automatic migration generation from models.
"""

from logging.config import fileConfig
import asyncio

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models to ensure they're registered
from apps.adapters.db.postgres import Base
from apps.adapters.db.models import (
    CampaignModel,
    MessageModel,
    TemplateModel,
    OptInModel,
    EventModel,
)
from apps.core.config import get_settings


# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get database URL from settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database.url)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
    
    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with connection"""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode"""
    # Force the URL into the config object to be sure
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = settings.database.url
    print(f"DEBUG: Migrating with URL: {settings.database.url.replace(settings.database.password, '***')}")
    
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode"""
    # Try sync instead of async for a moment to see if it helps
    sync_url = settings.database.url.replace("asyncpg", "psycopg2")
    print(f"DEBUG: Sync URL: {sync_url.replace(settings.database.password, '***')}")
    
    from sqlalchemy import create_engine
    connectable = create_engine(sync_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
