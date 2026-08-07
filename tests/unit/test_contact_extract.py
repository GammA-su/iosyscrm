"""Extraction de contacts depuis le site — section 6.4."""

from typing import Any

import httpx
import respx

from app.config import Settings
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.providers.base import ProviderContext
from app.services.enrichment.providers.contact_extract import (
    MIN_INTERVAL_SECONDS,
    ContactExtractProvider,
)

SITE = "https://exemple-contact.fr"

HOME = """
<!doctype html>
<html lang="fr">
  <body>
    <nav>
      <a href="/contact">Contact</a>
      <a href="/mentions-legales">Mentions légales</a>
      <a href="/tarifs">Tarifs</a>
      <a href="https://ailleurs.fr/contact">Partenaire</a>
    </nav>
    <p>Appelez-nous au 03 88 12 34 56</p>
  </body>
</html>
"""

CONTACT_PAGE = """
<!doctype html>
<html lang="fr">
  <body>
    <a href="mailto:contact@exemple-contact.fr?subject=Devis">Écrivez-nous</a>
    <a href="tel:+33612345678">06 12 34 56 78</a>
    <p>Service surtaxé : 08 92 70 12 34</p>
    <p>Ne pas utiliser : noreply@exemple-contact.fr</p>
  </body>
</html>
"""

LEGAL_PAGE = """
<!doctype html>
<html lang="fr">
  <body><p>Directeur de publication : claire.martin@exemple-contact.fr</p></body>
</html>
"""


class FakeClock:
    """Horloge et sommeil simulés, pour observer le cadencement."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENRICHMENT_TIMEOUT_SECONDS": 10,
        "ENRICHMENT_USER_AGENT": "IOSYS-ProspectBot/1.0 (+https://iosys.fr/bot)",
        "ENRICHMENT_RESPECT_ROBOTS": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _company() -> Company:
    return Company(id=1, siren="912345680", siret_siege="91234568000017")


def _provider(clock: FakeClock, **overrides: Any) -> ContactExtractProvider:
    return ContactExtractProvider(
        _settings(**overrides), sleep=clock.sleep, monotonic=clock.monotonic
    )


def _mock_site(robots: str) -> dict[str, respx.Route]:
    """Routes du site de test, renvoyées pour être inspectées.

    Deux pièges évités ici : une route respx sans chemin capture aussi les
    sous-pages (les chemins précis sont donc déclarés avant la racine), et
    ré-enregistrer une route identique remplace la précédente — il faut
    réutiliser l'objet renvoyé plutôt que d'appeler `respx.get` à nouveau.
    """
    return {
        "robots": respx.get(f"{SITE}/robots.txt").mock(
            return_value=httpx.Response(200, text=robots)
        ),
        "contact": respx.get(f"{SITE}/contact").mock(
            return_value=httpx.Response(200, html=CONTACT_PAGE)
        ),
        "legal": respx.get(f"{SITE}/mentions-legales").mock(
            return_value=httpx.Response(200, html=LEGAL_PAGE)
        ),
        "home": respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, html=HOME)),
    }


def test_robots_disallowing_contact_blocks_that_page() -> None:
    """La page interdite n'est pas demandée ; le reste de l'exploration suit."""
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        routes = _mock_site("User-agent: *\nDisallow: /contact\n")
        _provider(clock).run(_company(), context)

    assert routes["contact"].call_count == 0
    assert routes["legal"].call_count == 1
    values = {contact.value for contact in context.contacts}
    assert "contact@exemple-contact.fr" not in values
    assert "claire.martin@exemple-contact.fr" in values


def test_robots_is_ignored_when_the_setting_is_off() -> None:
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        routes = _mock_site("User-agent: *\nDisallow: /\n")
        _provider(clock, ENRICHMENT_RESPECT_ROBOTS=False).run(_company(), context)

    assert routes["robots"].call_count == 0
    assert routes["contact"].call_count == 1


def test_a_server_error_on_robots_stops_the_exploration() -> None:
    """5xx sur robots.txt : on ne présume pas de l'autorisation."""
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        respx.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(503))
        home_route = respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, html=HOME))
        _provider(clock).run(_company(), context)

    assert home_route.call_count == 0
    assert context.contacts == []


def test_only_linked_candidate_pages_are_explored() -> None:
    """Ni `/tarifs` (hors liste), ni le domaine tiers ne sont visités."""
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        _mock_site("User-agent: *\nAllow: /\n")
        _provider(clock).run(_company(), context)
        requested = [str(call.request.url) for call in respx.calls]

    assert f"{SITE}/tarifs" not in requested
    assert "https://ailleurs.fr/contact" not in requested
    # robots.txt + accueil + 2 pages candidates : 4 pages explorées au plus.
    assert len([url for url in requested if not url.endswith("robots.txt")]) == 3


def test_contacts_are_normalized_and_qualified() -> None:
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        _mock_site("User-agent: *\nAllow: /\n")
        facts = _provider(clock).run(_company(), context)

    emails = {c.value: c for c in context.contacts if c.channel == "email"}
    phones = {c.value for c in context.contacts if c.channel == "phone"}

    assert "contact@exemple-contact.fr" in emails
    assert emails["contact@exemple-contact.fr"].is_generic is True
    assert emails["claire.martin@exemple-contact.fr"].is_generic is False
    assert "noreply@exemple-contact.fr" not in emails
    assert phones == {"+33612345678", "+33388123456"}
    assert all(contact.origin == "website" for contact in context.contacts)
    assert all(contact.confidence == 0.60 for contact in context.contacts)

    assert facts[0].field == EnrichmentField.EMAIL_DOMAIN_PROFESSIONAL
    assert facts[0].value_json is True


def test_requests_are_spaced_by_one_second_per_domain() -> None:
    clock = FakeClock()
    context = ProviderContext(website_url=SITE)

    with respx.mock:
        _mock_site("User-agent: *\nAllow: /\n")
        _provider(clock).run(_company(), context)

    # robots.txt, accueil, /contact, /mentions-legales : trois attentes.
    assert clock.sleeps == [MIN_INTERVAL_SECONDS] * 3


def test_no_url_produces_nothing() -> None:
    clock = FakeClock()
    context = ProviderContext()

    assert _provider(clock).run(_company(), context) == []
    assert context.contacts == []
