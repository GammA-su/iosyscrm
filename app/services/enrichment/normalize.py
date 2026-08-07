"""Normalisation des emails, téléphones et URLs — section 6.4.

Aucune devinette n'est faite ici : ni format d'adresse à partir d'un nom, ni
nom de domaine à partir d'une raison sociale (interdits de la section 6.5).
Ce module ne fait que nettoyer et qualifier des valeurs déjà observées.
"""

from typing import Final
from urllib.parse import urlsplit, urlunsplit

import phonenumbers
from email_validator import EmailNotValidError, validate_email

#: Boîtes techniques : jamais un contact commercial (section 6.4).
REJECTED_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {"noreply", "no-reply", "webmaster", "postmaster", "abuse"}
)

#: Boîtes de service : exploitables, mais marquées `is_generic`.
GENERIC_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {"contact", "info", "commercial", "accueil", "bonjour", "hello"}
)

#: Domaines jetables : une adresse qui y pointe ne vaut rien.
DISPOSABLE_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "yopmail.com",
        "yopmail.fr",
        "mailinator.com",
        "guerrillamail.com",
        "trashmail.com",
        "jetable.org",
        "10minutemail.com",
        "throwawaymail.com",
        "tempmail.com",
        "temp-mail.org",
        "fakeinbox.com",
        "getnada.com",
    }
)

#: Domaines grand public : l'adresse est valide mais pas professionnelle.
CONSUMER_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "yahoo.fr",
        "orange.fr",
        "free.fr",
        "wanadoo.fr",
        "sfr.fr",
        "laposte.net",
        "hotmail.com",
        "hotmail.fr",
        "outlook.com",
        "outlook.fr",
        "live.com",
        "live.fr",
        "bbox.fr",
        "numericable.fr",
    }
)

#: Indicatif national des numéros surtaxés français.
PREMIUM_RATE_FR_PREFIX: Final = "8"
FRANCE_COUNTRY_CODE: Final = 33

ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
DEFAULT_URL_SCHEME: Final = "https"


def _local_and_domain(address: str) -> tuple[str, str]:
    local, _, domain = address.partition("@")
    return local, domain


def normalize_email(raw: str | None) -> str | None:
    """Adresse email exploitable en minuscules, ou `None`.

    Rejette les boîtes techniques (`noreply@`…) et les domaines jetables :
    écrire à ces adresses ne produit rien et abîme la réputation d'envoi.
    """
    if not raw:
        return None
    candidate = raw.strip().strip("<>").rstrip(".,;:").lower()
    if not candidate:
        return None
    try:
        validated = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None

    address = validated.normalized.lower()
    local, domain = _local_and_domain(address)
    if local in REJECTED_LOCAL_PARTS or domain in DISPOSABLE_DOMAINS:
        return None
    return address


def is_generic_email(address: str) -> bool:
    """Adresse de service (`contact@`, `info@`…) plutôt que nominative."""
    local, _ = _local_and_domain(address.lower())
    return local in GENERIC_LOCAL_PARTS


def is_professional_domain(address: str) -> bool:
    """`False` pour les messageries grand public (gmail, orange, free…)."""
    _, domain = _local_and_domain(address.lower())
    return bool(domain) and domain not in CONSUMER_DOMAINS


def normalize_phone(raw: str | None, region: str = "FR") -> str | None:
    """Numéro au format E.164, ou `None`.

    Les numéros surtaxés (08 en France, et tout numéro identifié comme
    `PREMIUM_RATE`) sont rejetés : les appeler coûte au prospect.
    """
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    if phonenumbers.number_type(parsed) == phonenumbers.PhoneNumberType.PREMIUM_RATE:
        return None
    if parsed.country_code == FRANCE_COUNTRY_CODE and str(parsed.national_number).startswith(
        PREMIUM_RATE_FR_PREFIX
    ):
        return None
    return str(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))


def normalize_url(raw: str | None) -> str | None:
    """URL http(s) canonique, ou `None`.

    Le schéma manquant est complété en `https`, l'hôte est mis en minuscules
    et le fragment est retiré. Aucun domaine n'est deviné : une entrée sans
    hôte exploitable est rejetée.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if "//" not in candidate:
        candidate = f"{DEFAULT_URL_SCHEME}://{candidate}"

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return None
    host = parts.hostname
    if not host or "." not in host:
        return None

    netloc = host.lower()
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    return urlunsplit((scheme, netloc, path, parts.query, ""))
