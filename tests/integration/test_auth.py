"""Authentification de bout en bout : sessions, rôles, redirection (section 9)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.deps import CurrentUser, get_db, require_role
from app.main import create_app
from app.models.user import User, UserSession
from app.repositories import user as user_repo
from app.services import auth

PASSWORD = "Motdepasse-integration-1"


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    auth.clear_rate_limiter()
    yield
    auth.clear_rate_limiter()


@pytest.fixture
def app(db_session: Session) -> FastAPI:
    """Application réelle, plus deux routes protégées servant de cible de test."""
    application = create_app()

    def override_get_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db

    @application.get("/_test/protected")
    def protected(user: CurrentUser) -> dict[str, str]:
        return {"email": user.email, "role": user.role}

    @application.get("/_test/admin-only", dependencies=[Depends(require_role("admin"))])
    def admin_only() -> dict[str, str]:
        return {"ok": "true"}

    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _create_user(
    db: Session,
    *,
    email: str,
    role: str = "commercial",
    password: str = PASSWORD,
) -> User:
    user = user_repo.add(
        db,
        User(
            email=email,
            password_hash=auth.hash_password(password),
            full_name="Compte de test",
            role=role,
        ),
    )
    db.commit()
    return user


def test_login_then_reach_a_protected_route(client: TestClient, db_session: Session) -> None:
    """Critère de validation du lot : création, connexion, accès protégé."""
    _create_user(db_session, email="commercial@iosys.fr")

    response = client.post(
        "/login",
        data={"email": "commercial@iosys.fr", "password": PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    cookie = response.cookies.get(auth.SESSION_COOKIE_NAME)
    assert cookie is not None

    protected = client.get("/_test/protected")
    assert protected.status_code == 200
    assert protected.json() == {"email": "commercial@iosys.fr", "role": "commercial"}


def test_session_cookie_carries_the_expected_attributes(
    client: TestClient, db_session: Session
) -> None:
    """HttpOnly, SameSite=Lax, 7 jours ; `Secure` seulement en production."""
    _create_user(db_session, email="cookie@iosys.fr")

    response = client.post(
        "/login",
        data={"email": "cookie@iosys.fr", "password": PASSWORD},
        follow_redirects=False,
    )
    set_cookie = response.headers["set-cookie"]

    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert f"Max-Age={7 * 24 * 3600}" in set_cookie
    assert "Secure" not in set_cookie


def test_no_cookie_is_refused(client: TestClient) -> None:
    assert client.get("/_test/protected").status_code == 401


def test_forged_cookie_is_refused(client: TestClient, db_session: Session) -> None:
    """Un jeton fabriqué ne correspond à aucune empreinte stockée."""
    _create_user(db_session, email="forge@iosys.fr")
    client.cookies.set(auth.SESSION_COOKIE_NAME, "jeton-fabrique-de-toutes-pieces")

    response = client.get("/_test/protected")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_expired_session_is_refused(client: TestClient, db_session: Session) -> None:
    user = _create_user(db_session, email="expire@iosys.fr")
    token = auth.create_session(db_session, user)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == auth.hash_token(token))
        .values(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    )
    db_session.commit()
    client.cookies.set(auth.SESSION_COOKIE_NAME, token)

    assert client.get("/_test/protected").status_code == 401


def test_deactivated_user_is_refused_even_with_a_valid_session(
    client: TestClient, db_session: Session
) -> None:
    user = _create_user(db_session, email="desactive@iosys.fr")
    token = auth.create_session(db_session, user)
    client.cookies.set(auth.SESSION_COOKIE_NAME, token)
    assert client.get("/_test/protected").status_code == 200

    user.is_active = False
    db_session.commit()

    assert client.get("/_test/protected").status_code == 401


def test_viewer_is_refused_on_an_admin_route(client: TestClient, db_session: Session) -> None:
    user = _create_user(db_session, email="viewer@iosys.fr", role="viewer")
    client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session(db_session, user))

    response = client.get("/_test/admin-only")

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_admin_is_allowed_on_an_admin_route(client: TestClient, db_session: Session) -> None:
    user = _create_user(db_session, email="admin@iosys.fr", role="admin")
    client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session(db_session, user))

    assert client.get("/_test/admin-only").status_code == 200


def test_session_is_extended_when_close_to_expiry(client: TestClient, db_session: Session) -> None:
    """Sous le seuil de prolongation, `expires_at` repart à +7 jours."""
    user = _create_user(db_session, email="glissante@iosys.fr")
    token = auth.create_session(db_session, user)
    almost_expired = datetime.now(tz=UTC) + timedelta(days=1)
    db_session.execute(
        update(UserSession)
        .where(UserSession.token_hash == auth.hash_token(token))
        .values(expires_at=almost_expired)
    )
    db_session.commit()
    client.cookies.set(auth.SESSION_COOKIE_NAME, token)

    assert client.get("/_test/protected").status_code == 200

    db_session.expire_all()
    stored = db_session.execute(
        UserSession.__table__.select().where(
            UserSession.__table__.c.token_hash == auth.hash_token(token)
        )
    ).one()
    assert stored.expires_at > almost_expired + timedelta(days=5)


def test_logout_revokes_the_session(client: TestClient, db_session: Session) -> None:
    user = _create_user(db_session, email="sortie@iosys.fr")
    client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session(db_session, user))

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/_test/protected").status_code == 401


def test_login_rejects_a_wrong_password(client: TestClient, db_session: Session) -> None:
    _create_user(db_session, email="mauvais@iosys.fr")

    response = client.post(
        "/login",
        data={"email": "mauvais@iosys.fr", "password": "pas-le-bon"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert auth.SESSION_COOKIE_NAME not in response.cookies


def test_login_is_throttled_after_five_failures(client: TestClient, db_session: Session) -> None:
    _create_user(db_session, email="quota@iosys.fr")
    payload = {"email": "quota@iosys.fr", "password": "pas-le-bon"}

    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        assert client.post("/login", data=payload, follow_redirects=False).status_code == 401

    blocked = client.post("/login", data=payload, follow_redirects=False)
    assert blocked.status_code == 429

    # Même le bon mot de passe est refusé tant que la fenêtre court.
    still_blocked = client.post(
        "/login",
        data={"email": "quota@iosys.fr", "password": PASSWORD},
        follow_redirects=False,
    )
    assert still_blocked.status_code == 429


def test_login_next_rejects_an_absolute_external_url(
    client: TestClient, db_session: Session
) -> None:
    """Pas de redirection ouverte : une cible externe retombe sur la racine."""
    _create_user(db_session, email="redirection@iosys.fr")

    response = client.post(
        "/login",
        data={
            "email": "redirection@iosys.fr",
            "password": PASSWORD,
            "next": "https://exemple-malveillant.fr/phishing",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_next_keeps_a_relative_target(client: TestClient, db_session: Session) -> None:
    _create_user(db_session, email="relatif@iosys.fr")

    response = client.post(
        "/login",
        data={
            "email": "relatif@iosys.fr",
            "password": PASSWORD,
            "next": "/prospects?stage=2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/prospects?stage=2"


def test_login_page_renders_without_cdn(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "/static/vendor/bootstrap/bootstrap.min.css" in response.text
    assert "cdn." not in response.text
