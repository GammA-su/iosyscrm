"""Extraction de contacts depuis le site — rang 3, confiance 0.60 (section 6.4).

Exploration délibérément étroite : page d'accueil, plus au maximum les pages
`/contact`, `/mentions-legales` et `/nous-contacter` **si elles sont liées
depuis l'accueil**. Profondeur 1, quatre pages au plus, une requête par
seconde et par domaine, `robots.txt` respecté.

Aucune source interdite par la section 6.5 n'est consultée, et aucune adresse
n'est devinée : seules les valeurs effectivement présentes sont retenues.
"""

import re
import time
from collections.abc import Callable, Iterable
from typing import Final
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import phonenumbers
from selectolax.parser import HTMLParser

from app.config import Settings
from app.logging import get_logger
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.normalize import (
    is_generic_email,
    is_professional_domain,
    normalize_email,
    normalize_phone,
)
from app.services.enrichment.providers.base import (
    ContactCandidate,
    FactCandidate,
    ProviderContext,
)

logger = get_logger(__name__)

PROVIDER_NAME: Final = "contact_extract"
PROVIDER_CONFIDENCE: Final = 0.60
CONTACT_ORIGIN: Final = "website"

#: Section 6.4 : profondeur 1, quatre pages au maximum, accueil compris.
MAX_PAGES: Final = 4
CANDIDATE_PATHS: Final[tuple[str, ...]] = ("/contact", "/mentions-legales", "/nous-contacter")

#: Section 6.4 : une requête par seconde et par domaine.
MIN_INTERVAL_SECONDS: Final = 1.0

PHONE_REGION: Final = "FR"

_EMAIL_PATTERN: Final = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _deny_all() -> RobotFileParser:
    """Analyseur refusant tout : `robots.txt` illisible ou serveur en panne.

    C'est la règle usuelle pour une réponse 5xx : on ne suppose pas
    l'autorisation quand le serveur n'est pas en mesure de la donner.
    """
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def _same_host(candidate: str, reference: str) -> bool:
    return urlsplit(candidate).netloc.lower() == urlsplit(reference).netloc.lower()


def _linked_candidate_pages(html: HTMLParser, base_url: str) -> list[str]:
    """Pages candidates effectivement liées depuis l'accueil, sans doublon."""
    found: list[str] = []
    for node in html.css("a[href]"):
        href = node.attributes.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href.strip())
        if not _same_host(absolute, base_url):
            continue
        path = urlsplit(absolute).path.rstrip("/").lower()
        if path in CANDIDATE_PATHS and absolute not in found:
            found.append(absolute)
    return found


def extract_emails(html: HTMLParser, body: str) -> list[str]:
    """Adresses trouvées dans les liens `mailto:` et dans le texte."""
    raw_addresses: list[str] = []
    for node in html.css('a[href^="mailto:"]'):
        href = node.attributes.get("href") or ""
        raw_addresses.append(href.removeprefix("mailto:").split("?", 1)[0])
    raw_addresses.extend(_EMAIL_PATTERN.findall(body))

    addresses: list[str] = []
    for raw in raw_addresses:
        address = normalize_email(raw)
        if address is not None and address not in addresses:
            addresses.append(address)
    return addresses


def extract_phones(html: HTMLParser, text: str) -> list[str]:
    """Numéros trouvés dans les liens `tel:` et dans le texte, en E.164."""
    raw_numbers: list[str] = [
        (node.attributes.get("href") or "").removeprefix("tel:")
        for node in html.css('a[href^="tel:"]')
    ]
    raw_numbers.extend(
        match.raw_string for match in phonenumbers.PhoneNumberMatcher(text, PHONE_REGION)
    )

    numbers: list[str] = []
    for raw in raw_numbers:
        number = normalize_phone(raw, PHONE_REGION)
        if number is not None and number not in numbers:
            numbers.append(number)
    return numbers


class ContactExtractProvider:
    """Explore le site à profondeur 1 et en tire des contacts."""

    name = PROVIDER_NAME
    confidence = PROVIDER_CONFIDENCE

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request_at: dict[str, float] = {}

    def _build_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=float(self._settings.ENRICHMENT_TIMEOUT_SECONDS),
            follow_redirects=True,
            headers={"User-Agent": self._settings.ENRICHMENT_USER_AGENT},
        )

    def _throttle(self, url: str) -> None:
        """Respecte l'intervalle minimal entre deux requêtes d'un même domaine."""
        host = urlsplit(url).netloc.lower()
        last = self._last_request_at.get(host)
        now = self._monotonic()
        if last is not None:
            remaining = MIN_INTERVAL_SECONDS - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at[host] = now

    def _robots_parser(self, client: httpx.Client, url: str) -> RobotFileParser | None:
        """`robots.txt` du domaine, mis en cache. `None` si tout est autorisé."""
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host in self._robots:
            return self._robots[host]

        parser: RobotFileParser | None = None
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        self._throttle(robots_url)
        try:
            response = client.get(robots_url)
        except httpx.HTTPError:
            # Domaine injoignable pour robots.txt : on n'explore pas à l'aveugle.
            parser = _deny_all()
        else:
            if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
                parser = _deny_all()
            elif response.status_code == httpx.codes.OK:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())

        self._robots[host] = parser
        return parser

    def _is_allowed(self, client: httpx.Client, url: str) -> bool:
        if not self._settings.ENRICHMENT_RESPECT_ROBOTS:
            return True
        parser = self._robots_parser(client, url)
        if parser is None:
            return True
        return parser.can_fetch(self._settings.ENRICHMENT_USER_AGENT, url)

    def _fetch(self, client: httpx.Client, url: str) -> str | None:
        if not self._is_allowed(client, url):
            logger.info("contact_extract.robots_denied", url=url)
            return None
        self._throttle(url)
        response = client.get(url)
        if response.status_code != httpx.codes.OK:
            logger.info("contact_extract.unexpected_status", url=url, status=response.status_code)
            return None
        return response.text

    def _pages(self, client: httpx.Client, home_url: str) -> Iterable[tuple[str, str]]:
        home_body = self._fetch(client, home_url)
        if home_body is None:
            return
        yield home_url, home_body

        html = HTMLParser(home_body)
        for url in _linked_candidate_pages(html, home_url)[: MAX_PAGES - 1]:
            body = self._fetch(client, url)
            if body is not None:
                yield url, body

    def run(self, company: Company, context: ProviderContext) -> list[FactCandidate]:
        """Contacts trouvés sur le site, et qualification du domaine email."""
        url = context.website_url
        if url is None:
            logger.info("contact_extract.no_url", siren=company.siren)
            return []

        client = self._client or self._build_client()
        emails: list[str] = []
        phones: list[str] = []
        try:
            for page_url, body in self._pages(client, url):
                html = HTMLParser(body)
                text = html.text(separator=" ")
                emails.extend(
                    address for address in extract_emails(html, body) if address not in emails
                )
                phones.extend(
                    number for number in extract_phones(html, text) if number not in phones
                )
                logger.debug("contact_extract.page", url=page_url)
        finally:
            if self._client is None:
                client.close()

        for address in emails:
            context.contacts.append(
                ContactCandidate(
                    channel="email",
                    value=address,
                    display_value=address,
                    origin=CONTACT_ORIGIN,
                    confidence=self.confidence,
                    is_generic=is_generic_email(address),
                )
            )
        for number in phones:
            context.contacts.append(
                ContactCandidate(
                    channel="phone",
                    value=number,
                    display_value=number,
                    origin=CONTACT_ORIGIN,
                    confidence=self.confidence,
                )
            )

        if not emails:
            return []
        return [
            FactCandidate(
                field=EnrichmentField.EMAIL_DOMAIN_PROFESSIONAL,
                value_json=is_professional_domain(emails[0]),
            )
        ]
