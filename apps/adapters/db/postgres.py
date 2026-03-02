"""
PostgreSQL Database Setup

Configures SQLAlchemy async engine, session factory, and base models.

Features:
    - Async SQLAlchemy with asyncpg
    - Connection pooling
    - Session management
    - Base model with common fields
    - Automated timestamps
"""

from typing import AsyncGenerator
from contextlib import asynccontextmanager
import logging

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func
from datetime import datetime

from apps.core.config import get_settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models"""
    
    # Common fields for all tables
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Database:
    """
    Database connection manager
    
    Handles engine creation, session management, and connection pooling.
    
    Example:
        >>> db = Database()
        >>> await db.connect()
        >>> async with db.session() as session:
        ...     result = await session.execute(query)
    """
    
    def __init__(self):
        """Initialize database"""
        self.settings = get_settings()
        self.engine = None
        self.session_factory = None
    
    async def connect(self) -> None:
        """Create database engine and session factory"""
        if self.engine:
            return
        
        try:
            # Create async engine
            url = self.settings.database.url
            obfuscated_url = url.replace(self.settings.database.password, "****")
            logger.info(f"Connecting to database: {obfuscated_url}")
            
            self.engine = create_async_engine(
                url,
                echo=self.settings.database.echo,
                pool_size=self.settings.database.pool_size,
                max_overflow=self.settings.database.max_overflow,
                pool_pre_ping=True,  # Verify connections before using
                pool_recycle=3600,  # Recycle connections after 1 hour
            )
            
            # Create session factory
            self.session_factory = async_sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            
            logger.info("Database connected")
            
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Close database connections"""
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None
            logger.info("Database disconnected")
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get database session context manager
        
        Yields:
            AsyncSession for database operations
            
        Example:
            >>> async with db.session() as session:
            ...     result = await session.execute(query)
            ...     await session.commit()
        """
        if not self.session_factory:
            raise RuntimeError("Database not connected")
        
        async with self.session_factory() as session:
            yield session
    
    async def create_tables(self) -> None:
        """Create all tables (for development only)"""
        if not self.engine:
            await self.connect()
        
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        logger.info("Database tables created")
    
    async def drop_tables(self) -> None:
        """Drop all tables (DANGEROUS - dev only)"""
        if not self.engine:
            await self.connect()
        
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        
        logger.warning("Database tables dropped")


# Global database instance
_db_instance: Database = None


def get_database() -> Database:
    """
    Get global database instance
    
    Returns:
        Database instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


async def init_database() -> None:
    """Initialize database on application startup"""
    db = get_database()
    await db.connect()


async def close_database() -> None:
    """Close database on application shutdown"""
    db = get_database()
    await db.disconnect()


# Dependency for FastAPI
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions
    
    Example:
        @app.get("/campaigns")
        async def get_campaigns(
            session: AsyncSession = Depends(get_db_session)
        ):
            result = await session.execute(query)
            return result.scalars().all()
    """
    db = get_database()
    async with db.session() as session:
        yield session
