"""Client HTTP bas niveau de l'API SIRENE — contraintes de la section 5.1.

Aucune écriture en base ici : ce module ne fait que parler à l'API, respecter
le quota et traduire les codes de retour en exceptions typées.

Les sept contraintes de la section 5.1 sont toutes matérialisées dans ce
fichier : seau à jetons partagé, pagination par curseur uniquement, jamais de
`tri` ni de `debut`, page plafonnée à 1000, en-tête
`X-INSEE-Api-Key-Integration`, paramètre `champs` systématique, et un
traitement distinct de chaque code de retour.
"""

import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings, get_settings
from app.exceptions import ExternalServiceError
from app.logging import get_logger
from app.services.sirene.parser import parse_api_datetime

logger = get_logger(__name__)

#: Collection dont dépend la collecte : le collecteur n'interroge que `/siret`
#: (section 5.2). Comparée sous forme normalisée, sans accent ni casse.
ETABLISSEMENTS_COLLECTION: Final = "etablissements"

#: En-tête d'authentification imposé par l'API (section 5.1, point 5).
API_KEY_HEADER: Final = "X-INSEE-Api-Key-Integration"

#: Taille de page maximale acceptée par l'API (section 5.1, point 4).
MAX_PAGE_SIZE: Final = 1000

#: Curseur d'amorçage de la pagination (section 5.1, point 2).
INITIAL_CURSOR: Final = "*"

TIMEOUT_SECONDS: Final = 30.0
RETRY_ATTEMPTS: Final = 5
RETRY_INITIAL_SECONDS: Final = 2.0
RETRY_MAX_SECONDS: Final = 60.0

#: Colonnes réellement stockées dans `companies` (section 3.2). Le paramètre
#: `champs` limite la charge utile à celles-ci et à rien d'autre
#: (section 5.1, point 6).
CHAMPS: Final[tuple[str, ...]] = (
    "siren",
    "siret",
    "etablissementSiege",
    "statutDiffusionEtablissement",
    "dateCreationEtablissement",
    "dateDernierTraitementEtablissement",
    "etatAdministratifEtablissement",
    # NAF 2025 alimente `activite_principale` ; le code de la période courante
    # et sa nomenclature ne servent que de repli (section 5.3).
    "activitePrincipaleNAF25Etablissement",
    "activitePrincipaleEtablissement",
    "nomenclatureActivitePrincipaleEtablissement",
    "trancheEffectifsEtablissement",
    "numeroVoieEtablissement",
    "typeVoieEtablissement",
    "libelleVoieEtablissement",
    "complementAdresseEtablissement",
    "codePostalEtablissement",
    "libelleCommuneEtablissement",
    "codeCommuneEtablissement",
    "denominationUniteLegale",
    "nomUniteLegale",
    "prenomUsuelUniteLegale",
    "categorieJuridiqueUniteLegale",
    "trancheEffectifsUniteLegale",
)

CHAMPS_PARAM: Final = ",".join(CHAMPS)


class SireneError(ExternalServiceError):
    """Échec d'un appel à l'API SIRENE."""

    code = "sirene_error"


class QueryError(SireneError):
    """400 — requête malformée. Rejouer à l'identique ne servirait à rien."""

    code = "sirene_query_error"


class AuthError(SireneError):
    """401 — clé d'API invalide ou absente. Arrêt immédiat."""

    code = "sirene_auth_error"


class QueryTooLongError(SireneError):
    """414 — URL trop longue, la requête doit être réduite."""

    code = "sirene_query_too_long"


class QuotaError(SireneError):
    """429 — quota dépassé. Rejouable après attente."""

    code = "sirene_quota_error"


class ServerError(SireneError):
    """5xx — panne côté INSEE. Rejouable."""

    code = "sirene_server_error"


#: Seules ces deux erreurs justifient un retry (section 5.1, point 7).
RETRYABLE_ERRORS: Final = (QuotaError, ServerError)


class RateLimiter:
    """Seau à jetons partagé par tout le processus (section 5.1, point 1).

    Le quota de l'API est de 30 requêtes par minute ; la configuration retient
    28 pour garder une marge. `acquire()` bloque tant qu'aucun jeton n'est
    disponible, et est sûr entre threads : l'enrichissement s'exécute dans un
    `ThreadPoolExecutor` (section 1.2).
    """

    def __init__(
        self,
        rate_per_minute: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("Le quota doit être strictement positif.")
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._refill_per_second = rate_per_minute / 60.0
        self._monotonic = monotonic
        self._sleep = sleep
        self._updated_at = monotonic()
        self._lock = threading.Lock()

    @property
    def available_tokens(self) -> float:
        """Jetons disponibles à l'instant présent, sans en consommer."""
        with self._lock:
            self._refill()
            return self._tokens

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = now - self._updated_at
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
            self._updated_at = now

    def acquire(self) -> None:
        """Consomme un jeton, en attendant qu'il s'en libère un si nécessaire."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_seconds = (1.0 - self._tokens) / self._refill_per_second
            self._sleep(wait_seconds)


@dataclass(frozen=True, slots=True)
class SirenePage:
    """Une page de résultats du service `siret`."""

    total: int
    curseur: str
    curseur_suivant: str
    etablissements: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_last(self) -> bool:
        """Fin de pagination : l'API renvoie le curseur qu'on lui a envoyé."""
        return self.curseur_suivant == self.curseur


@dataclass(frozen=True, slots=True)
class InformationsOut:
    """Réponse du service `informations`, réduite à ce qui pilote la collecte.

    Tout ce qui est exposé ici porte sur la collection des **établissements** :
    c'est la seule qu'interroge le collecteur.
    """

    #: Repère de fraîcheur des données (watermark `sirene.last_disposition`).
    date_derniere_mise_a_disposition: datetime
    #: Borne haute exacte de la fenêtre de synchronisation. Préférable à
    #: `now()`, qui couvrirait une plage où aucune donnée n'existe encore.
    date_dernier_traitement_maximum: datetime | None
    #: État de la collection « Établissements » : hors « UP », rien à collecter.
    etat_collection: str | None
    version: str | None
    etat_service: str | None
    raw: dict[str, Any]


def _normalize_collection(value: Any) -> str:
    """Nom de collection insensible à la casse et aux accents.

    L'API écrit « Établissements » ; comparer la chaîne telle quelle rendrait
    la lecture du watermark dépendante d'un accent.
    """
    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return (
        "".join(char for char in decomposed if not unicodedata.combining(char)).strip().casefold()
    )


def _find_etablissements(entries: Any, collection_key: str) -> dict[str, Any] | None:
    """Entrée relative à la collection « Établissements », ou `None`."""
    if not isinstance(entries, list):
        return None
    for item in entries:
        if not isinstance(item, dict):
            continue
        label = item.get(collection_key)
        if _normalize_collection(label) == ETABLISSEMENTS_COLLECTION:
            return item
    return None


class SireneClient:
    """Client synchrone de l'API SIRENE, connexions réutilisées."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.SIRENE_BASE_URL.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=TIMEOUT_SECONDS,
            headers={
                API_KEY_HEADER: self._settings.SIRENE_API_KEY,
                "Accept": "application/json",
            },
        )
        self._limiter = limiter or RateLimiter(self._settings.SIRENE_RATE_LIMIT_PER_MINUTE)
        self._retrying: Retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential_jitter(initial=RETRY_INITIAL_SECONDS, max=RETRY_MAX_SECONDS),
            reraise=True,
            sleep=sleep,
        )

    # --- Cycle de vie ---------------------------------------------------

    def close(self) -> None:
        """Ferme le client HTTP si ce client l'a lui-même créé."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SireneClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- Transport ------------------------------------------------------

    def _handle_status(self, response: httpx.Response, path: str) -> None:
        """Traduit le code de retour en exception, code par code (section 5.1)."""
        status = response.status_code
        if status in (httpx.codes.OK, httpx.codes.NOT_FOUND):
            return
        if status == httpx.codes.MOVED_PERMANENTLY:
            # L'unité a un successeur. Ce n'est pas une erreur : on journalise
            # et on rend la réponse telle quelle, à charge de l'appelant de
            # suivre le lien de succession.
            logger.warning(
                "sirene.succession",
                path=path,
                location=response.headers.get("location"),
            )
            return
        if status == httpx.codes.BAD_REQUEST:
            raise QueryError(f"Requête SIRENE malformée : {response.text[:200]}")
        if status == httpx.codes.UNAUTHORIZED:
            raise AuthError("Clé d'API SIRENE invalide ou absente.")
        if status == httpx.codes.REQUEST_URI_TOO_LONG:
            raise QueryTooLongError("Requête SIRENE trop longue, il faut la découper.")
        if status == httpx.codes.TOO_MANY_REQUESTS:
            raise QuotaError("Quota SIRENE dépassé.")
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise ServerError(f"Erreur serveur SIRENE ({status}).")
        raise SireneError(f"Réponse SIRENE inattendue ({status}).")

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Un appel, quota respecté. Lève une erreur typée selon le code."""
        self._limiter.acquire()
        response = self._client.get(f"{self._base_url}{path}", params=params)
        self._handle_status(response, path)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SireneError("Réponse SIRENE illisible (JSON invalide).") from exc
        if not isinstance(payload, dict):
            raise SireneError("Réponse SIRENE inattendue : objet JSON attendu.")
        return payload

    def _request_with_retry(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Même appel, avec 5 tentatives sur 429 et 5xx uniquement."""
        return self._retrying(self._request, path, params)

    # --- Services -------------------------------------------------------

    def get_informations(self) -> InformationsOut:
        """Date de mise à disposition de la collection des ÉTABLISSEMENTS (5.2).

        Seule cette collection compte : le collecteur n'interroge que `/siret`.
        Se caler sur une autre ferait avancer le watermark sans qu'aucune
        donnée établissement n'ait bougé, et la synchronisation correspondante
        serait purement et simplement sautée. Une collection absente ou
        illisible est donc une erreur, pas un cas à contourner.
        """
        payload = self._request_with_retry("/informations", {})

        # Attention : la clé de collection est en minuscule dans
        # `datesDernieresMisesAJourDesDonnees` et en majuscule dans
        # `etatsDesServices`.
        entry = _find_etablissements(
            payload.get("datesDernieresMisesAJourDesDonnees"), "collection"
        )
        if entry is None:
            raise SireneError(
                "Le service informations ne renvoie pas la collection "
                f"« {ETABLISSEMENTS_COLLECTION} » : watermark inexploitable."
            )

        disposition = parse_api_datetime(entry.get("dateDerniereMiseADisposition"))
        if disposition is None:
            raise SireneError(
                "Date de mise à disposition des établissements absente ou illisible : "
                f"{entry.get('dateDerniereMiseADisposition')!r}."
            )

        etat_entry = _find_etablissements(payload.get("etatsDesServices"), "Collection")

        return InformationsOut(
            date_derniere_mise_a_disposition=disposition,
            date_dernier_traitement_maximum=parse_api_datetime(
                entry.get("dateDernierTraitementMaximum")
            ),
            etat_collection=etat_entry.get("etatCollection") if etat_entry else None,
            version=payload.get("versionService"),
            etat_service=payload.get("etatService"),
            raw=payload,
        )

    def search_siret(
        self,
        query: str,
        cursor: str = INITIAL_CURSOR,
        size: int | None = None,
    ) -> SirenePage:
        """Une page du service `siret`, paginée PAR CURSEUR.

        Ni `debut` ni `tri` ne sont transmis : l'offset est proscrit et `tri`
        est incompatible avec `curseur` (section 5.1, points 2 et 3).
        """
        page_size = min(size or self._settings.SIRENE_PAGE_SIZE, MAX_PAGE_SIZE)
        params: dict[str, Any] = {
            "q": query,
            "nombre": page_size,
            "curseur": cursor,
            "champs": CHAMPS_PARAM,
        }
        payload = self._request_with_retry("/siret", params)
        header = payload.get("header") or {}

        if header.get("statut") == httpx.codes.NOT_FOUND:
            # Aucun résultat : ce n'est pas une erreur (section 5.1, point 7).
            logger.info("sirene.no_result", query=query)
            return SirenePage(total=0, curseur=cursor, curseur_suivant=cursor)

        etablissements = [
            item for item in payload.get("etablissements") or [] if isinstance(item, dict)
        ]
        sent_cursor = str(header.get("curseur") or cursor)
        next_cursor = header.get("curseurSuivant")
        return SirenePage(
            total=int(header.get("total") or 0),
            curseur=sent_cursor,
            curseur_suivant=str(next_cursor) if next_cursor else sent_cursor,
            etablissements=etablissements,
        )

    def iter_siret(self, query: str) -> Iterator[dict[str, Any]]:
        """Parcourt toutes les pages, du premier curseur au dernier.

        La boucle s'arrête quand l'API renvoie le curseur qui lui a été envoyé,
        ou quand une page ne contient plus rien.
        """
        cursor = INITIAL_CURSOR
        while True:
            page = self.search_siret(query, cursor=cursor)
            yield from page.etablissements
            if page.is_last or not page.etablissements:
                return
            cursor = page.curseur_suivant
