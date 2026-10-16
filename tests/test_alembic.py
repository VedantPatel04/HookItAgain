import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from hookitagain.settings import get_settings


@pytest.mark.skipif(not os.getenv("CI"), reason="Alembic CI postgres only")
def test_alembic_upgrade_head_creates_contract_tables() -> None:
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")

    raw = str(get_settings().database_direct_url)
    if raw.startswith("postgres://"):
        url = "postgresql+psycopg://" + raw.removeprefix("postgres://")
    elif raw.startswith("postgresql://"):
        url = "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    else:
        url = raw

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"alembic_version", "projects", "events", "outbox_jobs"} <= tables
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "0001_initial_schema"
