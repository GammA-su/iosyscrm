"""Fixtures d'intégration : PostgreSQL réel via testcontainers.

Pas de SQLite : le schéma utilise JSONB, `DISTINCT ON` et des index partiels
(section 13.1).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.docker_client import DockerClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:17-alpine"


def docker_available() -> bool:
    """Indique si un démon Docker est joignable."""
    try:
        DockerClient().client.ping()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Ignore les tests d'intégration si aucun démon Docker n'est joignable."""
    if docker_available():
        return
    skip = pytest.mark.skip(reason="Docker indisponible : tests d'intégration ignorés")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Instance PostgreSQL jetable, vide, dédiée à la session de tests."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    """Configuration Alembic pointant sur la base jetable."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def migrated_engine(alembic_config: Config, database_url: str) -> Iterator[Engine]:
    """Engine sur une base migrée jusqu'à `head`."""
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    """Connexion dont la transaction est annulée en fin de test."""
    with migrated_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def db_session(connection: Connection) -> Iterator[Session]:
    """Session ORM greffée sur la transaction du test.

    `join_transaction_mode="create_savepoint"` fait que les `commit()` du code
    applicatif relâchent un point de reprise sans valider la transaction
    externe : le test reste annulable intégralement.
    """
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
