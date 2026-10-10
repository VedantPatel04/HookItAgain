from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from hookitagain.settings import get_settings


def _runtime_url() -> str:
    raw = str(get_settings().database_url)
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgres://")
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    return raw


@lru_cache
def get_engine() -> Engine:
    return create_engine(_runtime_url(), pool_size=1, max_overflow=0)


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
