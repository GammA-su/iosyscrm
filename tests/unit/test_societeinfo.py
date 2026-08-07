"""Fournisseur societeinfo — rang 1, confiance 0.90 (sections 6.1 et 8).

`GET {base}/company.json/{siren}?key=<clé>`.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.providers.base import ProviderContext
from app.services.enrichment.providers.societeinfo import (
    SocieteInfoProvider,
    _build_request,
    _map_response,
    sanitize_payload,
)

BASE_URL = "https://societeinfo.test/app/rest/api/v2"
SIREN = "912345680"
API_KEY = "cle-test"
FixtureLoader = Callable[[str], dict[str, Any]]


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "SOCIETEINFO_ENABLED": True,
        "SOCIETEINFO_API_KEY": API_KEY,
        "SOCIETEINFO_BASE_URL": BASE_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _company(*, is_personne_physique: bool = False) -> Company:
    return Company(
        id=1,
        siren=SIREN,
        siret_siege=f"{SIREN}00017",
        is_personne_physique=is_personne_physique,
    )


def _route(payload: dict[str, Any]) -> respx.Route:
    return respx.get(f"{BASE_URL}/company.json/{SIREN}").mock(
        return_value=httpx.Response(200, json=payload)
    )


# --- Contrat d'appel ----------------------------------------------------


def test_request_uses_the_documented_path_and_query_key() -> None:
    url, params = _build_request(BASE_URL, API_KEY, SIREN)

    assert url == f"{BASE_URL}/company.json/{SIREN}"
    assert params == {"key": API_KEY}


def test_provider_authenticates_by_query_parameter(load_fixture: FixtureLoader) -> None:
    """La clé passe en paramètre de requête, jamais en en-tête."""
    with respx.mock:
        route = _route(load_fixture("societeinfo/entreprise.json"))
        SocieteInfoProvider(_settings()).run(_company(), ProviderContext())

    request = route.calls[0].request
    assert request.url.params["key"] == API_KEY
    assert "authorization" not in request.headers
    # Aucun paramètre optionnel n'est activé.
    assert set(request.url.params.keys()) == {"key"}


# --- Mapping ------------------------------------------------------------


def test_provider_maps_the_documented_payload(load_fixture: FixtureLoader) -> None:
    context = ProviderContext()
    with respx.mock:
        _route(load_fixture("societeinfo/entreprise.json"))
        facts = SocieteInfoProvider(_settings()).run(_company(), context)

    by_field = {fact.field: fact for fact in facts}
    assert by_field[EnrichmentField.WEBSITE_URL].value == (
        "https://strasbourg-immobilier-conseil.fr"
    )
    assert by_field[EnrichmentField.HAS_WEBSITE].value_json is True
    assert by_field[EnrichmentField.EFFECTIF_ESTIME].value_json == 3
    assert by_field[EnrichmentField.DIRIGEANT_PRESENT].value_json is True
    assert by_field[EnrichmentField.EMAIL_DOMAIN_PROFESSIONAL].value_json is True
    assert context.website_url == "https://strasbourg-immobilier-conseil.fr"


def test_has_website_is_false_when_no_url_is_returned() -> None:
    with respx.mock:
        _route({"organization": {"web_infos": {"website_url": None}}})
        facts = SocieteInfoProvider(_settings()).run(_company(), ProviderContext())

    by_field = {fact.field: fact for fact in facts}
    assert by_field[EnrichmentField.HAS_WEBSITE].value_json is False
    assert EnrichmentField.WEBSITE_URL not in by_field


def test_principal_email_is_primary_and_generic_ones_follow(
    load_fixture: FixtureLoader,
) -> None:
    context = ProviderContext()
    with respx.mock:
        _route(load_fixture("societeinfo/entreprise.json"))
        SocieteInfoProvider(_settings()).run(_company(), context)

    emails = [contact for contact in context.contacts if contact.channel == "email"]

    assert [contact.value for contact in emails] == [
        "claire.martin@strasbourg-immobilier-conseil.fr",
        "contact@strasbourg-immobilier-conseil.fr",
    ]
    assert emails[0].is_primary is True
    assert emails[0].is_generic is False
    assert emails[1].is_primary is False
    assert emails[1].is_generic is True
    # `noreply@` est écarté par la normalisation (section 6.4).
    assert all("noreply" not in contact.value for contact in emails)


def test_phones_are_normalized_and_premium_rate_dropped(
    load_fixture: FixtureLoader,
) -> None:
    context = ProviderContext()
    with respx.mock:
        _route(load_fixture("societeinfo/entreprise.json"))
        SocieteInfoProvider(_settings()).run(_company(), context)

    phones = [contact for contact in context.contacts if contact.channel == "phone"]

    assert [contact.value for contact in phones] == ["+33388123456"]
    assert all(contact.origin == "societeinfo" for contact in context.contacts)
    assert all(contact.confidence == 0.90 for contact in context.contacts)


def test_dirigeant_present_is_false_without_a_main_officier() -> None:
    with respx.mock:
        _route({"organization": {"contacts": {}}})
        facts = SocieteInfoProvider(_settings()).run(_company(), ProviderContext())

    by_field = {fact.field: fact for fact in facts}
    assert by_field[EnrichmentField.DIRIGEANT_PRESENT].value_json is False


def test_social_networks_are_never_mapped(load_fixture: FixtureLoader) -> None:
    """Ni LinkedIn, ni Facebook, ni Twitter, ni Wikipedia : aucune finalité."""
    payload = load_fixture("societeinfo/entreprise.json")
    assert payload["organization"]["web_infos"]["linkedin"]

    context = ProviderContext()
    with respx.mock:
        _route(payload)
        facts = SocieteInfoProvider(_settings()).run(_company(), context)

    rendered = repr(facts) + repr(context.contacts)
    for marker in ("linkedin", "facebook", "twitter", "wikipedia"):
        assert marker not in rendered.lower()


# --- Contrôle de cohérence personne physique ---------------------------


def test_person_type_mismatch_is_logged_without_changing_the_value(
    load_fixture: FixtureLoader,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SIRENE fait foi : la divergence est signalée, pas corrigée."""
    payload = load_fixture("societeinfo/entreprise.json")
    payload["organization"]["legal"]["person_type"] = "Individual"
    company = _company(is_personne_physique=False)

    with respx.mock:
        _route(payload)
        SocieteInfoProvider(_settings()).run(company, ProviderContext())

    assert company.is_personne_physique is False
    assert "societeinfo.person_type_mismatch" in capsys.readouterr().out


def test_matching_person_type_logs_nothing(
    load_fixture: FixtureLoader,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = load_fixture("societeinfo/entreprise.json")
    payload["organization"]["legal"]["person_type"] = "Individual"

    with respx.mock:
        _route(payload)
        SocieteInfoProvider(_settings()).run(_company(is_personne_physique=True), ProviderContext())

    assert "person_type_mismatch" not in capsys.readouterr().out


# --- Minimisation (section 8) ------------------------------------------


def test_sanitize_payload_strips_every_forbidden_block(
    load_fixture: FixtureLoader,
) -> None:
    payload = load_fixture("societeinfo/entreprise.json")

    sanitized = sanitize_payload(payload)

    organization = sanitized["organization"]
    assert "beneficiaires_effectifs" not in organization
    assert "corporate_officiers" not in organization["contacts"]
    assert "corporate_officiers" not in sanitized["contacts"]
    officer = organization["contacts"]["main_corporate_officier"]
    assert set(officer) == {"role"}
    # La réponse d'origine n'est pas modifiée.
    assert "beneficiaires_effectifs" in payload["organization"]


def test_no_personal_value_survives_the_fetch(load_fixture: FixtureLoader) -> None:
    """Aucune donnée nominative ne circule au-delà de la réception."""
    payload = load_fixture("societeinfo/entreprise.json")
    context = ProviderContext()

    with respx.mock:
        _route(payload)
        facts = SocieteInfoProvider(_settings()).run(_company(), context)

    rendered = repr(facts) + repr(context.contacts)
    for forbidden in (
        "1978-04-12",
        "1982-11-03",
        "DURAND",
        "pourcentage_parts",
        "details_parts_indirectes",
        "details_votes_indirects",
        "perso-exemple.fr",
    ):
        assert forbidden not in rendered


def test_sanitize_payload_tolerates_missing_blocks() -> None:
    assert sanitize_payload({}) == {}
    assert sanitize_payload({"organization": "pas-un-objet"}) == {"organization": "pas-un-objet"}


# --- Robustesse ---------------------------------------------------------


def test_provider_is_silent_when_not_found() -> None:
    context = ProviderContext()
    with respx.mock:
        respx.get(f"{BASE_URL}/company.json/{SIREN}").mock(return_value=httpx.Response(404))
        facts = SocieteInfoProvider(_settings()).run(_company(), context)

    assert facts == []
    assert context.contacts == []


def test_provider_raises_on_a_server_error() -> None:
    """L'orchestrateur doit voir l'échec pour le tracer en `failed`."""
    with respx.mock:
        respx.get(f"{BASE_URL}/company.json/{SIREN}").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            SocieteInfoProvider(_settings()).run(_company(), ProviderContext())


@pytest.mark.parametrize(
    "overrides",
    [
        {"SOCIETEINFO_ENABLED": False},
        {"SOCIETEINFO_API_KEY": ""},
        {"SOCIETEINFO_BASE_URL": ""},
    ],
)
def test_provider_does_nothing_when_not_configured(overrides: dict[str, Any]) -> None:
    with respx.mock:
        route = respx.get(f"{BASE_URL}/company.json/{SIREN}")
        facts = SocieteInfoProvider(_settings(**overrides)).run(_company(), ProviderContext())

    assert facts == []
    assert route.call_count == 0


def test_map_response_tolerates_an_unexpected_shape() -> None:
    data = _map_response({"inconnu": True})

    assert data.website_url is None
    assert data.principal_email is None
    assert data.generic_emails == ()
    assert data.phones == ()
    assert data.effectif is None
    assert data.dirigeant_present is None
    assert data.person_type is None


def test_map_response_accepts_a_textual_staff_count() -> None:
    assert _map_response({"financials": {"last_staff": "12"}}).effectif == 12
    assert _map_response({"financials": {"last_staff": "environ 12"}}).effectif is None
    assert _map_response({"financials": {"last_staff": True}}).effectif is None
