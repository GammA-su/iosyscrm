"""Fournisseur societeinfo — rang 1, confiance 0.90 (section 6.1).

Source primaire : le service est déjà payé et ses données sont
contractuellement exploitables. Reconstruire un collecteur de contacts maison
serait moins fiable et juridiquement plus exposé.

`GET {base}/company.json/{registration_number}?key=<clé>`, où
`registration_number` est le SIREN à 9 chiffres — le service renvoie alors le
siège. Aucun paramètre optionnel n'est activé.

**Minimisation (section 8).** La réponse contient des personnes physiques
identifiées, avec dates de naissance et parts sociales. Ces blocs n'ont aucune
finalité dans un CRM de prospection : ils sont retirés par `sanitize_payload`
dès la réception, avant même le mapping. Seul `dirigeant_present`, un booléen,
est retenu de ce périmètre.
"""

import copy
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.config import Settings
from app.logging import get_logger
from app.models.company import Company
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.normalize import (
    is_generic_email,
    is_professional_domain,
    normalize_email,
    normalize_phone,
    normalize_url,
)
from app.services.enrichment.providers.base import (
    ContactCandidate,
    FactCandidate,
    ProviderContext,
)

logger = get_logger(__name__)

PROVIDER_NAME: Final = "societeinfo"
PROVIDER_CONFIDENCE: Final = 0.90
CONTACT_ORIGIN: Final = "societeinfo"
REQUEST_TIMEOUT_SECONDS: Final = 15.0

#: `organization.legal.person_type` d'un entrepreneur individuel.
PERSON_TYPE_INDIVIDUAL: Final = "Individual"

#: Blocs de `organization` jamais mappés ni stockés : personnes physiques
#: identifiées, dates de naissance, parts sociales.
FORBIDDEN_ORGANIZATION_KEYS: Final[tuple[str, ...]] = ("beneficiaires_effectifs",)

#: Blocs de `organization.contacts` et de `contacts` au même régime.
FORBIDDEN_CONTACT_BLOCK_KEYS: Final[tuple[str, ...]] = ("corporate_officiers",)

#: Attributs nominatifs du dirigeant : seule sa PRÉSENCE est exploitée.
FORBIDDEN_OFFICER_KEYS: Final[tuple[str, ...]] = (
    "birth_date",
    "firstName",
    "lastName",
    "name",
)

#: Réseaux sociaux : aucune valeur du vocabulaire fermé ne les accueille et
#: nous n'en avons aucun usage. Collecter sans finalité reste exclu.
UNMAPPED_WEB_INFOS: Final[tuple[str, ...]] = ("linkedin", "facebook", "twitter", "wikipedia")


@dataclass(frozen=True, slots=True)
class SocieteInfoData:
    """Réponse societeinfo ramenée au vocabulaire du projet."""

    website_url: str | None = None
    principal_email: str | None = None
    generic_emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    effectif: int | None = None
    dirigeant_present: bool | None = None
    person_type: str | None = None


def _build_request(
    base_url: str, api_key: str, registration_number: str
) -> tuple[str, dict[str, str]]:
    """URL et paramètres de requête de l'appel par numéro d'immatriculation.

    L'authentification passe par le paramètre `key`, pas par un en-tête.
    """
    url = f"{base_url.rstrip('/')}/company.json/{registration_number}"
    return url, {"key": api_key}


def sanitize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Copie de la réponse, expurgée des données personnelles (section 8).

    Appliquée dès la réception : les valeurs interdites ne parviennent même
    pas au mapping, et ne peuvent donc atteindre ni un fait, ni un contact,
    ni un stockage brut.
    """
    sanitized = copy.deepcopy(raw)

    organization = sanitized.get("organization")
    if isinstance(organization, dict):
        for key in FORBIDDEN_ORGANIZATION_KEYS:
            organization.pop(key, None)
        _sanitize_contacts_block(organization.get("contacts"))

    _sanitize_contacts_block(sanitized.get("contacts"))
    return sanitized


def _sanitize_contacts_block(block: Any) -> None:
    """Retire les mandataires sociaux et les attributs nominatifs du dirigeant."""
    if not isinstance(block, dict):
        return
    for key in FORBIDDEN_CONTACT_BLOCK_KEYS:
        block.pop(key, None)
    officer = block.get("main_corporate_officier")
    if isinstance(officer, dict):
        for key in FORBIDDEN_OFFICER_KEYS:
            officer.pop(key, None)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _map_response(raw: dict[str, Any]) -> SocieteInfoData:
    """Traduit une réponse DÉJÀ expurgée en données normalisées.

    Les réseaux sociaux de `web_infos` sont délibérément ignorés
    (`UNMAPPED_WEB_INFOS`).
    """
    organization = raw.get("organization")
    organization = organization if isinstance(organization, dict) else {}
    web_infos = organization.get("web_infos")
    web_infos = web_infos if isinstance(web_infos, dict) else {}
    legal = organization.get("legal")
    legal = legal if isinstance(legal, dict) else {}
    organization_contacts = organization.get("contacts")
    organization_contacts = organization_contacts if isinstance(organization_contacts, dict) else {}

    contacts = raw.get("contacts")
    contacts = contacts if isinstance(contacts, dict) else {}
    financials = raw.get("financials")
    financials = financials if isinstance(financials, dict) else {}

    dirigeant_present: bool | None = None
    if organization:
        dirigeant_present = bool(organization_contacts.get("main_corporate_officier"))

    return SocieteInfoData(
        website_url=normalize_url(web_infos.get("website_url")),
        principal_email=contacts.get("email") if isinstance(contacts.get("email"), str) else None,
        generic_emails=_as_str_tuple(contacts.get("emails")),
        phones=_as_str_tuple(contacts.get("phones")),
        effectif=_as_int(financials.get("last_staff")),
        dirigeant_present=dirigeant_present,
        person_type=legal.get("person_type") if isinstance(legal.get("person_type"), str) else None,
    )


class SocieteInfoProvider:
    """Interroge societeinfo par SIREN et en tire faits et contacts."""

    name = PROVIDER_NAME
    confidence = PROVIDER_CONFIDENCE

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def is_configured(self) -> bool:
        """Le fournisseur ne s'exécute que s'il est activé ET paramétré."""
        return bool(
            self._settings.SOCIETEINFO_ENABLED
            and self._settings.SOCIETEINFO_API_KEY
            and self._settings.SOCIETEINFO_BASE_URL
        )

    def _fetch(self, siren: str) -> dict[str, Any] | None:
        url, params = _build_request(
            self._settings.SOCIETEINFO_BASE_URL,
            self._settings.SOCIETEINFO_API_KEY,
            siren,
        )
        client = self._client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response = client.get(url, params=params)
        finally:
            if self._client is None:
                client.close()

        if response.status_code == httpx.codes.NOT_FOUND:
            logger.info("societeinfo.not_found", siren=siren)
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        # Expurgation immédiate : rien d'interdit ne circule au-delà.
        return sanitize_payload(payload)

    def _check_person_type(self, company: Company, data: SocieteInfoData) -> None:
        """Contrôle de cohérence. SIRENE fait foi, la valeur n'est pas modifiée."""
        if data.person_type is None:
            return
        societeinfo_says_individual = data.person_type == PERSON_TYPE_INDIVIDUAL
        if societeinfo_says_individual != company.is_personne_physique:
            logger.warning(
                "societeinfo.person_type_mismatch",
                siren=company.siren,
                societeinfo_person_type=data.person_type,
                sirene_is_personne_physique=company.is_personne_physique,
            )

    def _email_facts_and_contacts(
        self, data: SocieteInfoData, context: ProviderContext
    ) -> list[FactCandidate]:
        """Email principal d'abord, puis les adresses de service."""
        facts: list[FactCandidate] = []
        seen: set[str] = set()
        candidates: list[tuple[str, bool]] = []
        if data.principal_email is not None:
            candidates.append((data.principal_email, True))
        candidates.extend((raw_email, False) for raw_email in data.generic_emails)

        for raw_email, is_principal in candidates:
            address = normalize_email(raw_email)
            if address is None or address in seen:
                continue
            seen.add(address)
            context.contacts.append(
                ContactCandidate(
                    channel="email",
                    value=address,
                    display_value=raw_email.strip(),
                    origin=CONTACT_ORIGIN,
                    confidence=self.confidence,
                    # Les adresses secondaires de societeinfo sont des boîtes
                    # de service ; la règle de la section 6.4 s'applique aussi.
                    is_generic=is_generic_email(address) or not is_principal,
                    is_primary=is_principal,
                )
            )
            if is_principal or len(facts) == 0:
                facts = [
                    FactCandidate(
                        field=EnrichmentField.EMAIL_DOMAIN_PROFESSIONAL,
                        value_json=is_professional_domain(address),
                    )
                ]
        return facts

    def run(self, company: Company, context: ProviderContext) -> list[FactCandidate]:
        """Faits et contacts connus de societeinfo pour cette entreprise."""
        if not self.is_configured:
            logger.info("societeinfo.disabled", siren=company.siren)
            return []

        raw = self._fetch(company.siren)
        if raw is None:
            return []
        data = _map_response(raw)
        self._check_person_type(company, data)

        facts: list[FactCandidate] = [
            FactCandidate(
                field=EnrichmentField.HAS_WEBSITE,
                value_json=data.website_url is not None,
            )
        ]
        if data.website_url is not None:
            facts.append(FactCandidate(field=EnrichmentField.WEBSITE_URL, value=data.website_url))
            context.website_url = data.website_url
        if data.effectif is not None:
            facts.append(
                FactCandidate(
                    field=EnrichmentField.EFFECTIF_ESTIME,
                    value=str(data.effectif),
                    value_json=data.effectif,
                )
            )
        if data.dirigeant_present is not None:
            facts.append(
                FactCandidate(
                    field=EnrichmentField.DIRIGEANT_PRESENT,
                    value_json=data.dirigeant_present,
                )
            )

        facts.extend(self._email_facts_and_contacts(data, context))

        for raw_phone in data.phones:
            number = normalize_phone(raw_phone)
            if number is None:
                continue
            context.contacts.append(
                ContactCandidate(
                    channel="phone",
                    value=number,
                    display_value=raw_phone.strip(),
                    origin=CONTACT_ORIGIN,
                    confidence=self.confidence,
                )
            )

        return facts
