"""Normalisation des emails, téléphones et URLs — section 6.4."""

import pytest

from app.services.enrichment.normalize import (
    is_generic_email,
    is_professional_domain,
    normalize_email,
    normalize_phone,
    normalize_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Contact@Exemple.FR", "contact@exemple.fr"),
        ("  jean.dupont@exemple.fr  ", "jean.dupont@exemple.fr"),
        ("<info@exemple.fr>", "info@exemple.fr"),
        ("commercial@exemple.fr.", "commercial@exemple.fr"),
    ],
)
def test_normalize_email_lowercases_and_trims(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "noreply@exemple.fr",
        "no-reply@exemple.fr",
        "webmaster@exemple.fr",
        "postmaster@exemple.fr",
        "abuse@exemple.fr",
    ],
)
def test_normalize_email_rejects_technical_mailboxes(raw: str) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize("raw", ["jetable@yopmail.com", "test@mailinator.com"])
def test_normalize_email_rejects_disposable_domains(raw: str) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize("raw", [None, "", "pas-une-adresse", "a@b", "@exemple.fr"])
def test_normalize_email_rejects_invalid_values(raw: str | None) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize(
    "address",
    [
        "contact@exemple.fr",
        "info@exemple.fr",
        "commercial@exemple.fr",
        "accueil@exemple.fr",
        "bonjour@exemple.fr",
        "hello@exemple.fr",
    ],
)
def test_is_generic_email_detects_service_mailboxes(address: str) -> None:
    assert is_generic_email(address) is True


def test_is_generic_email_is_false_for_a_personal_address() -> None:
    assert is_generic_email("claire.martin@exemple.fr") is False


@pytest.mark.parametrize(
    "address",
    [
        "contact@gmail.com",
        "contact@yahoo.fr",
        "contact@orange.fr",
        "contact@free.fr",
        "contact@wanadoo.fr",
        "contact@sfr.fr",
        "contact@laposte.net",
        "contact@hotmail.com",
        "contact@outlook.fr",
        "contact@live.com",
        "contact@bbox.fr",
        "contact@numericable.fr",
    ],
)
def test_is_professional_domain_is_false_for_consumer_mailboxes(address: str) -> None:
    assert is_professional_domain(address) is False


def test_is_professional_domain_is_true_for_a_company_domain() -> None:
    assert is_professional_domain("contact@iosys.fr") is True


def test_normalize_phone_rejects_premium_rate_numbers() -> None:
    """Les 08 sont surtaxés : les appeler coûte au prospect (section 6.4)."""
    assert normalize_phone("08 92 70 12 34") is None
    assert normalize_phone("0899123456") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("06 12 34 56 78", "+33612345678"),
        ("06.12.34.56.78", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("03 88 12 34 56", "+33388123456"),
    ],
)
def test_normalize_phone_returns_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "12", "pas un numéro"])
def test_normalize_phone_rejects_invalid_values(raw: str | None) -> None:
    assert normalize_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("exemple.fr", "https://exemple.fr"),
        ("https://Exemple.FR/", "https://exemple.fr"),
        ("http://exemple.fr/contact/", "http://exemple.fr/contact"),
        ("https://exemple.fr/page#ancre", "https://exemple.fr/page"),
        ("https://exemple.fr/recherche?q=1", "https://exemple.fr/recherche?q=1"),
    ],
)
def test_normalize_url_canonicalises(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "ftp://exemple.fr", "javascript:alert(1)", "localhost"])
def test_normalize_url_rejects_unusable_values(raw: str | None) -> None:
    assert normalize_url(raw) is None
