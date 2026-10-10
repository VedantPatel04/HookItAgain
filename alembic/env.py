from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from hookitagain.db.models import Base
from hookitagain.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def direct_url() -> str:
    """Return DATABASE_DIRECT_URL rewritten for psycopg3 on a session connection."""
    raw = str(get_settings().database_direct_url)
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgres://")
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    return raw


def run_migrations_offline() -> None:
    """Emit SQL for the current revision without opening a live database connection."""
    context.configure(
        url=direct_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply revisions against Postgres using a short-lived NullPool engine."""
    connectable = create_engine(direct_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
