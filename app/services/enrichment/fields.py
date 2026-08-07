"""Vocabulaire fermé de `enrichment_facts.field` — section 3.4.

Les douze valeurs ci-dessous sont les seules autorisées. La base ne porte
aucune contrainte sur cette colonne : c'est donc à l'écriture qu'il faut la
faire respecter, sans quoi la table de faits accumulerait silencieusement des
champs jamais relus.
"""

from enum import StrEnum


class EnrichmentField(StrEnum):
    """Champ d'un fait d'enrichissement."""

    WEBSITE_URL = "website_url"
    HAS_WEBSITE = "has_website"
    WEBSITE_HTTPS = "website_https"
    WEBSITE_RESPONSIVE = "website_responsive"
    WEBSITE_CMS = "website_cms"
    WEBSITE_COPYRIGHT_YEAR = "website_copyright_year"
    WEBSITE_TTFB_MS = "website_ttfb_ms"
    WEBSITE_STATUS_CODE = "website_status_code"
    WEBSITE_QUALITY_SCORE = "website_quality_score"
    EMAIL_DOMAIN_PROFESSIONAL = "email_domain_professional"
    EFFECTIF_ESTIME = "effectif_estime"
    DIRIGEANT_PRESENT = "dirigeant_present"


def is_known_field(field: str) -> bool:
    """Indique si `field` appartient au vocabulaire de la section 3.4."""
    return field in set(EnrichmentField)
