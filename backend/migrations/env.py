"""Alembic environment.

Deliberately does NOT read the database URL from alembic.ini: the app already
resolves it from the environment (see app/config.py), and having two sources of
truth is how you end up migrating your laptop's SQLite while production waits.

    backend/.venv/bin/alembic revision --autogenerate -m "what changed"
    backend/.venv/bin/alembic upgrade head

    # against Neon, same as seeding:
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/alembic upgrade head
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# the app package lives one level up from migrations/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings          # noqa: E402
from app.db import Base                      # noqa: E402
from app import models                       # noqa: E402,F401  (registers the tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

# what --autogenerate diffs the database against
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it - useful for review."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the
            # table instead. Ignored on Postgres, so local and Neon stay in step.
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
