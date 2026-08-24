"""Async SQLAlchemy engine. One shared engine per process."""
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
