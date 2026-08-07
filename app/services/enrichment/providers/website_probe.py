"""Sonde technique du site — rang 2, confiance 0.95 (sections 6.2 et 6.3).

Un score inférieur à 50 est le signal commercial le plus intéressant : le
prospect a un site, il investit donc déjà, mais il est mauvais.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx
from selectolax.parser import HTMLParser

from app.config import Settings
from app.logging import get_logger
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.providers.base import FactCandidate, ProviderContext

logger = get_logger(__name__)

PROVIDER_NAME: Final = "website_probe"
PROVIDER_CONFIDENCE: Final = 0.95

#: Section 6.2 : au plus trois redirections suivies.
MAX_REDIRECTS: Final = 3

#: Barème de la section 6.3, sur 100.
POINTS_HTTPS: Final = 20
POINTS_VIEWPORT: Final = 20
POINTS_FAST_TTFB: Final = 15
POINTS_RECENT_COPYRIGHT: Final = 15
POINTS_STATUS_OK: Final = 15
POINTS_CMS_NOT_OBSOLETE: Final = 15

TTFB_FAST_THRESHOLD_MS: Final = 800
COPYRIGHT_MAX_AGE_YEARS: Final = 3
OLDEST_PLAUSIBLE_COPYRIGHT_YEAR: Final = 1990

#: Empreintes de chemins, testées après la balise `generator`.
PATH_FINGERPRINTS: Final[tuple[tuple[str, str], ...]] = (
    ("/wp-content/", "wordpress"),
    ("/_next/", "next.js"),
    ("/media/jui/", "joomla"),
    ("/sites/default/files/", "drupal"),
)

#: Noms de CMS reconnus dans une balise `generator`.
GENERATOR_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("wordpress", "wordpress"),
    ("joomla", "joomla"),
    ("drupal", "drupal"),
    ("prestashop", "prestashop"),
    ("shopify", "shopify"),
    ("wix", "wix"),
    ("typo3", "typo3"),
    ("spip", "spip"),
)

_VERSION_PATTERN: Final = re.compile(r"(\d+)(?:\.\d+)*")
_YEAR_PATTERN: Final = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass(frozen=True, slots=True)
class CmsDetection:
    """CMS identifié et, si elle est lisible, sa version majeure."""

    name: str | None = None
    major_version: int | None = None


def detect_cms(html: HTMLParser, body: str) -> CmsDetection:
    """CMS du site : balise `generator` d'abord, empreintes de chemins ensuite."""
    generator = html.css_first('meta[name="generator"]')
    content = (generator.attributes.get("content") or "") if generator else ""
    lowered = content.lower()
    for marker, name in GENERATOR_MARKERS:
        if marker in lowered:
            match = _VERSION_PATTERN.search(lowered.split(marker, 1)[1])
            return CmsDetection(name=name, major_version=int(match.group(1)) if match else None)

    for fingerprint, name in PATH_FINGERPRINTS:
        if fingerprint in body:
            return CmsDetection(name=name)

    return CmsDetection()


def detect_copyright_year(html: HTMLParser, current_year: int) -> int | None:
    """Plus grande année plausible trouvée dans le pied de page.

    À défaut de balise `<footer>`, la recherche porte sur tout le document :
    beaucoup de sites anciens n'utilisent pas cette balise.
    """
    footers = html.css("footer")
    text = " ".join(node.text(separator=" ") for node in footers) if footers else html.text()
    years = [
        year
        for raw in _YEAR_PATTERN.findall(text)
        if OLDEST_PLAUSIBLE_COPYRIGHT_YEAR <= (year := int(raw)) <= current_year
    ]
    return max(years) if years else None


def is_obsolete_cms(cms: CmsDetection, *, responsive: bool) -> bool:
    """Section 6.3 : ni Joomla 3, ni WordPress sans balise viewport."""
    if cms.name == "joomla" and cms.major_version == 3:
        return True
    return cms.name == "wordpress" and not responsive


def compute_quality_score(
    *,
    https: bool,
    responsive: bool,
    ttfb_ms: int,
    copyright_year: int | None,
    status_code: int,
    cms: CmsDetection,
    current_year: int,
) -> int:
    """Score sur 100 selon la grille de la section 6.3."""
    score = 0
    if https:
        score += POINTS_HTTPS
    if responsive:
        score += POINTS_VIEWPORT
    if ttfb_ms < TTFB_FAST_THRESHOLD_MS:
        score += POINTS_FAST_TTFB
    if copyright_year is not None and current_year - copyright_year < COPYRIGHT_MAX_AGE_YEARS:
        score += POINTS_RECENT_COPYRIGHT
    if status_code == httpx.codes.OK:
        score += POINTS_STATUS_OK
    if not is_obsolete_cms(cms, responsive=responsive):
        score += POINTS_CMS_NOT_OBSOLETE
    return score


class WebsiteProbeProvider:
    """Mesure la qualité technique du site découvert."""

    name = PROVIDER_NAME
    confidence = PROVIDER_CONFIDENCE

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._settings = settings
        self._client = client
        self._monotonic = monotonic
        self._now = now

    def _build_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=float(self._settings.ENRICHMENT_TIMEOUT_SECONDS),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            headers={"User-Agent": self._settings.ENRICHMENT_USER_AGENT},
        )

    def run(self, company: Company, context: ProviderContext) -> list[FactCandidate]:
        """Signaux techniques du site, plus le score de qualité."""
        url = context.website_url
        if url is None:
            logger.info("website_probe.no_url", siren=company.siren)
            return []

        client = self._client or self._build_client()
        try:
            started_at = self._monotonic()
            with client.stream("GET", url) as response:
                # Les en-têtes sont reçus : c'est le temps jusqu'au premier octet.
                ttfb_ms = int((self._monotonic() - started_at) * 1000)
                response.read()
                status_code = response.status_code
                final_url = response.url
                body = response.text
        finally:
            if self._client is None:
                client.close()

        html = HTMLParser(body)
        https = final_url.scheme == "https"
        responsive = html.css_first('meta[name="viewport"]') is not None
        cms = detect_cms(html, body)
        current_year = self._now().year
        copyright_year = detect_copyright_year(html, current_year)
        score = compute_quality_score(
            https=https,
            responsive=responsive,
            ttfb_ms=ttfb_ms,
            copyright_year=copyright_year,
            status_code=status_code,
            cms=cms,
            current_year=current_year,
        )

        facts = [
            FactCandidate(
                field=EnrichmentField.WEBSITE_STATUS_CODE,
                value=str(status_code),
                value_json=status_code,
            ),
            FactCandidate(field=EnrichmentField.WEBSITE_HTTPS, value_json=https),
            FactCandidate(
                field=EnrichmentField.WEBSITE_TTFB_MS, value=str(ttfb_ms), value_json=ttfb_ms
            ),
            FactCandidate(field=EnrichmentField.WEBSITE_RESPONSIVE, value_json=responsive),
            FactCandidate(
                field=EnrichmentField.WEBSITE_QUALITY_SCORE, value=str(score), value_json=score
            ),
        ]
        if cms.name is not None:
            facts.append(FactCandidate(field=EnrichmentField.WEBSITE_CMS, value=cms.name))
        if copyright_year is not None:
            facts.append(
                FactCandidate(
                    field=EnrichmentField.WEBSITE_COPYRIGHT_YEAR,
                    value=str(copyright_year),
                    value_json=copyright_year,
                )
            )
        return facts
