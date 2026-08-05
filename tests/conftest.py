"""Fixtures partagées."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.database.engine import get_engine, get_session_factory
from app.main import create_app

TEST_ENV: dict[str, str] = {
    "APP_ENV": "development",
    "APP_SECRET_KEY": "k" * 64,
    "APP_BASE_URL": "http://testserver",
    "APP_LOG_LEVEL": "WARNING",
    "DATABASE_URL": "postgresql+psycopg://crm:crm@localhost:5432/prospectcrm_test",
    "MAIL_ENABLED": "false",
}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Configuration de test, isolée du `.env` du poste."""
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    yield get_settings()

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Application FastAPI construite avec la configuration de test."""
    assert settings.APP_ENV == "development"
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Client HTTP de test."""
    with TestClient(app) as test_client:
        yield test_client
