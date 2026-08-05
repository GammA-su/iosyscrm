"""Environnement Alembic, branché sur Base.metadata et Settings.DATABASE_URL."""

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (importe tous les modèles dans Base.metadata)
from app.config import get_settings
from app.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Objets gérés en SQL brut dans les migrations, invisibles pour l'autogenerate.
#: Alembic ne sait pas comparer une vue : sans cette exclusion, chaque
#: `revision --autogenerate` proposerait de la supprimer.
EXCLUDED_OBJECTS: frozenset[str] = frozenset({"company_facts"})


def get_url() -> str:
    """URL de connexion : celle passée par le CLI si présente, sinon la configuration."""
    url = config.get_main_option("sqlalchemy.url", None)
    if url:
        return url
    return get_settings().DATABASE_URL


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Exclut la vue `company_facts` de la comparaison de schéma."""
    if name in EXCLUDED_OBJECTS:
        return False
    table_name = getattr(getattr(obj, "table", None), "name", None)
    return table_name not in EXCLUDED_OBJECTS


def run_migrations_offline() -> None:
    """Génère le SQL sans connexion (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Exécute les migrations sur une connexion ouverte."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Ouvre une connexion synchrone et applique les migrations."""
    engine = create_engine(get_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
