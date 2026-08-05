"""Service d'authentification : hachage et limitation des tentatives."""

from collections.abc import Iterator

import pytest

from app.api.web.auth import safe_next
from app.services import auth


@pytest.fixture(autouse=True)
def _clean_rate_limiter() -> Iterator[None]:
    """Le compteur de tentatives est un état de processus : on l'isole."""
    auth.clear_rate_limiter()
    yield
    auth.clear_rate_limiter()


def test_hash_password_is_argon2id_and_salted() -> None:
    first = auth.hash_password("correct horse battery staple")
    second = auth.hash_password("correct horse battery staple")

    assert first.startswith("$argon2id$")
    assert first != second


def test_verify_password_accepts_the_right_password() -> None:
    password_hash = auth.hash_password("Motdepasse-1")

    assert auth.verify_password(password_hash, "Motdepasse-1") is True


def test_verify_password_rejects_a_wrong_password() -> None:
    password_hash = auth.hash_password("Motdepasse-1")

    assert auth.verify_password(password_hash, "Motdepasse-2") is False


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert auth.verify_password("pas-une-empreinte", "Motdepasse-1") is False


def test_token_hash_is_sha256_hex() -> None:
    digest = auth.hash_token("jeton")

    assert len(digest) == 64
    assert digest == auth.hash_token("jeton")
    assert digest != auth.hash_token("jeton2")


def test_rate_limiter_trips_after_five_failures() -> None:
    email = "commercial@iosys.fr"

    for _ in range(auth.LOGIN_MAX_ATTEMPTS - 1):
        auth.record_failed_attempt(email)
    assert auth.is_rate_limited(email) is False

    auth.record_failed_attempt(email)
    assert auth.is_rate_limited(email) is True


def test_rate_limiter_is_per_email() -> None:
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_attempt("premier@iosys.fr")

    assert auth.is_rate_limited("premier@iosys.fr") is True
    assert auth.is_rate_limited("second@iosys.fr") is False


def test_rate_limiter_ignores_attempts_outside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    email = "ancien@iosys.fr"
    past = datetime.now(tz=UTC) - auth.LOGIN_WINDOW - timedelta(minutes=1)
    monkeypatch.setattr(auth, "_now", lambda: past)
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_attempt(email)

    monkeypatch.undo()

    assert auth.is_rate_limited(email) is False


def test_successful_login_resets_the_counter() -> None:
    email = "reset@iosys.fr"
    for _ in range(auth.LOGIN_MAX_ATTEMPTS):
        auth.record_failed_attempt(email)

    auth.reset_failed_attempts(email)

    assert auth.is_rate_limited(email) is False


@pytest.mark.parametrize(
    "candidate",
    [
        "https://exemple-malveillant.fr/phishing",
        "//exemple-malveillant.fr/phishing",
        "/\\exemple-malveillant.fr",
        "javascript:alert(1)",
        "prospects",
        "",
        None,
    ],
)
def test_safe_next_rejects_anything_but_a_site_relative_url(candidate: str | None) -> None:
    assert safe_next(candidate) == "/"


@pytest.mark.parametrize("candidate", ["/", "/prospects", "/prospects/12?tab=faits"])
def test_safe_next_keeps_site_relative_urls(candidate: str) -> None:
    assert safe_next(candidate) == candidate
