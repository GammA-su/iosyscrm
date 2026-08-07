"""Contrat commun des fournisseurs d'enrichissement — section 6.1.

Un fournisseur ne touche jamais la base : il lit une entreprise, interroge sa
source, et renvoie des candidats. C'est l'orchestrateur qui décide de ce qui
est écrit, et sous quelle confiance. Cela rend les fournisseurs exécutables
dans un `ThreadPoolExecutor` sans se soucier des sessions SQLAlchemy.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models.company import Company

#: Clé du contexte partagé où les fournisseurs déposent les contacts trouvés.
CONTACTS_KEY = "contacts"

#: Clé du contexte partagé portant l'URL du site, alimentée par societeinfo
#: puis consommée par website_probe et contact_extract.
WEBSITE_URL_KEY = "website_url"


@dataclass(frozen=True, slots=True)
class FactCandidate:
    """Fait proposé par un fournisseur, avant validation et écriture."""

    field: str
    value: str | None = None
    value_json: Any | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ContactCandidate:
    """Contact proposé par un fournisseur, déjà normalisé."""

    channel: str
    value: str
    display_value: str
    origin: str
    confidence: float
    is_generic: bool = False
    #: Contact principal du canal. `idx_contact_primary` n'en tolère qu'un par
    #: entreprise et par canal : la bascule est faite à l'écriture.
    is_primary: bool = False


@dataclass(slots=True)
class ProviderContext:
    """État partagé entre fournisseurs le temps d'une entreprise."""

    website_url: str | None = None
    contacts: list[ContactCandidate] = field(default_factory=list)


class EnrichmentProvider(Protocol):
    """Fournisseur exécuté par l'orchestrateur."""

    name: str
    confidence: float

    def run(self, company: Company, context: ProviderContext) -> list[FactCandidate]:
        """Interroge la source et renvoie les faits trouvés."""
        ...
