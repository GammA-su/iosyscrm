"""Sonde de site et barème de qualité — sections 6.2 et 6.3."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from selectolax.parser import HTMLParser

from app.config import Settings
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.providers.base import ProviderContext
from app.services.enrichment.providers.website_probe import (
    CmsDetection,
    WebsiteProbeProvider,
    compute_quality_score,
    detect_cms,
    detect_copyright_year,
    is_obsolete_cms,
)

CURRENT_YEAR = 2026
NOW = datetime(CURRENT_YEAR, 8, 6, 12, 0, tzinfo=UTC)

GOOD_SITE = """
<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="generator" content="WordPress 6.4.2">
    <title>Alsace Numérique</title>
  </head>
  <body>
    <main>Nos prestations</main>
    <footer>© 2026 Alsace Numérique — tous droits réservés</footer>
  </body>
</html>
"""

BAD_SITE = """
<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8">
    <title>Menuiserie Belfort</title>
  </head>
  <body>
    <p>Bienvenue</p>
    <footer>Copyright 2014 Menuiserie Belfort</footer>
  </body>
</html>
"""

JOOMLA3_SITE = """
<!doctype html>
<html lang="fr">
  <head>
    <meta name="viewport" content="width=device-width">
    <meta name="generator" content="Joomla! 3.9.28 - Open Source Content Management">
  </head>
  <body><footer>© 2026</footer></body>
</html>
"""


class FakeClock:
    """Horloge monotone : chaque appel avance du pas demandé."""

    def __init__(self, step_seconds: float) -> None:
        self.now = 0.0
        self._step = step_seconds

    def __call__(self) -> float:
        value = self.now
        self.now += self._step
        return value


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENRICHMENT_TIMEOUT_SECONDS": 10,
        "ENRICHMENT_USER_AGENT": "IOSYS-ProspectBot/1.0 (+https://iosys.fr/bot)",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _company() -> Company:
    return Company(id=1, siren="912345680", siret_siege="91234568000017")


def _probe(ttfb_seconds: float = 0.1) -> WebsiteProbeProvider:
    return WebsiteProbeProvider(
        _settings(),
        monotonic=FakeClock(ttfb_seconds),
        now=lambda: NOW,
    )


def _run(url: str, html: str, *, ttfb_seconds: float = 0.1) -> dict[str, Any]:
    context = ProviderContext(website_url=url)
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, html=html))
        facts = _probe(ttfb_seconds).run(_company(), context)
    return {fact.field: fact for fact in facts}


# --- Barème de la section 6.3 ------------------------------------------


def test_a_good_site_scores_the_full_grid() -> None:
    """HTTPS + viewport + TTFB rapide + copyright récent + 200 + CMS à jour."""
    facts = _run("https://exemple-bon.fr", GOOD_SITE)

    assert facts[EnrichmentField.WEBSITE_HTTPS].value_json is True
    assert facts[EnrichmentField.WEBSITE_RESPONSIVE].value_json is True
    assert facts[EnrichmentField.WEBSITE_STATUS_CODE].value_json == 200
    assert facts[EnrichmentField.WEBSITE_TTFB_MS].value_json == 100
    assert facts[EnrichmentField.WEBSITE_COPYRIGHT_YEAR].value_json == 2026
    assert facts[EnrichmentField.WEBSITE_CMS].value == "wordpress"
    # 20 + 20 + 15 + 15 + 15 + 15
    assert facts[EnrichmentField.WEBSITE_QUALITY_SCORE].value_json == 100


def test_a_weak_site_scores_only_the_criteria_it_meets() -> None:
    """HTTP, sans viewport, copyright 2014 : seuls TTFB, code 200 et CMS comptent."""
    facts = _run("http://exemple-mauvais.fr", BAD_SITE)

    assert facts[EnrichmentField.WEBSITE_HTTPS].value_json is False
    assert facts[EnrichmentField.WEBSITE_RESPONSIVE].value_json is False
    assert facts[EnrichmentField.WEBSITE_COPYRIGHT_YEAR].value_json == 2014
    assert EnrichmentField.WEBSITE_CMS not in facts
    # 0 + 0 + 15 (TTFB) + 0 + 15 (code 200) + 15 (CMS non obsolète) = 45
    assert facts[EnrichmentField.WEBSITE_QUALITY_SCORE].value_json == 45


def test_a_slow_site_loses_the_ttfb_points() -> None:
    facts = _run("https://exemple-lent.fr", GOOD_SITE, ttfb_seconds=1.2)

    assert facts[EnrichmentField.WEBSITE_TTFB_MS].value_json == 1200
    assert facts[EnrichmentField.WEBSITE_QUALITY_SCORE].value_json == 85


def test_joomla_3_loses_the_cms_points() -> None:
    facts = _run("https://exemple-joomla.fr", JOOMLA3_SITE)

    assert facts[EnrichmentField.WEBSITE_CMS].value == "joomla"
    # 20 + 20 + 15 + 15 + 15 + 0
    assert facts[EnrichmentField.WEBSITE_QUALITY_SCORE].value_json == 85


@pytest.mark.parametrize(
    ("cms", "responsive", "expected"),
    [
        (CmsDetection(name="joomla", major_version=3), True, True),
        (CmsDetection(name="joomla", major_version=5), True, False),
        (CmsDetection(name="wordpress"), False, True),
        (CmsDetection(name="wordpress"), True, False),
        (CmsDetection(), False, False),
    ],
)
def test_obsolete_cms_rule(cms: CmsDetection, responsive: bool, expected: bool) -> None:
    assert is_obsolete_cms(cms, responsive=responsive) is expected


def test_copyright_of_exactly_three_years_is_not_recent() -> None:
    """« Moins de 3 ans » : 2023 ne compte pas en 2026, 2024 oui."""
    common: dict[str, Any] = {
        "https": False,
        "responsive": False,
        "ttfb_ms": 5000,
        "status_code": 404,
        "cms": CmsDetection(),
        "current_year": CURRENT_YEAR,
    }
    assert compute_quality_score(copyright_year=2023, **common) == 15
    assert compute_quality_score(copyright_year=2024, **common) == 30


# --- Détection ----------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<img src="/wp-content/uploads/logo.png">', "wordpress"),
        ('<script src="/_next/static/chunk.js"></script>', "next.js"),
        ('<link href="/media/jui/css/style.css">', "joomla"),
        ('<img src="/sites/default/files/logo.png">', "drupal"),
    ],
)
def test_cms_is_detected_from_path_fingerprints(body: str, expected: str) -> None:
    assert detect_cms(HTMLParser(body), body).name == expected


def test_generator_wins_over_fingerprints() -> None:
    body = '<meta name="generator" content="Drupal 10"><img src="/wp-content/x.png">'
    detection = detect_cms(HTMLParser(body), body)

    assert detection.name == "drupal"
    assert detection.major_version == 10


def test_copyright_year_is_read_from_the_footer() -> None:
    body = "<body><p>fondée en 1998</p><footer>© 2019 - 2024 Exemple</footer></body>"

    assert detect_copyright_year(HTMLParser(body), CURRENT_YEAR) == 2024


def test_copyright_year_ignores_future_years() -> None:
    body = "<footer>© 2099 Exemple</footer>"

    assert detect_copyright_year(HTMLParser(body), CURRENT_YEAR) is None


def test_probe_without_url_produces_nothing() -> None:
    assert _probe().run(_company(), ProviderContext()) == []


def test_probe_follows_redirects_and_reports_the_final_scheme() -> None:
    """Le schéma retenu est celui de l'URL finale, pas celui demandé."""
    context = ProviderContext(website_url="http://exemple-redirige.fr")
    with respx.mock:
        respx.get("http://exemple-redirige.fr").mock(
            return_value=httpx.Response(301, headers={"location": "https://exemple-redirige.fr/"})
        )
        respx.get("https://exemple-redirige.fr/").mock(
            return_value=httpx.Response(200, html=GOOD_SITE)
        )
        facts = {fact.field: fact for fact in _probe().run(_company(), context)}

    assert facts[EnrichmentField.WEBSITE_HTTPS].value_json is True


def test_transport_errors_are_not_swallowed() -> None:
    """L'orchestrateur doit voir l'échec pour le tracer en `failed`."""
    context = ProviderContext(website_url="https://exemple-injoignable.fr")
    with respx.mock:
        respx.get("https://exemple-injoignable.fr").mock(
            side_effect=httpx.ConnectError("injoignable")
        )
        with pytest.raises(httpx.ConnectError):
            _probe().run(_company(), context)
