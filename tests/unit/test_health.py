"""Sonde /health."""

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.deps import get_db


class FakeSession:
    """Session minimale : exécute ou échoue, selon la configuration du test."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if self.fail:
            raise SQLAlchemyError("connexion impossible")
        return None

    def close(self) -> None:
        return None


def _override_db(app: FastAPI, session: FakeSession) -> None:
    def _get_db() -> Iterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _get_db


def test_health_ok(app: FastAPI, client: TestClient) -> None:
    session = FakeSession()
    _override_db(app, session)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": __version__}
    assert len(session.statements) == 1


def test_health_degraded_when_database_unreachable(app: FastAPI, client: TestClient) -> None:
    _override_db(app, FakeSession(fail=True))

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error", "version": __version__}
