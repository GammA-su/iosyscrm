"""Neutralise le marqueur « [ND] » stocké dans companies.

L'API SIRENE ne retire pas les champs non diffusibles : elle renvoie la chaîne
littérale « [ND] ». Les lignes collectées avant la correction du parser la
portent telle quelle, et `nom_complet` vaut même « [ND] [ND] », valeur produite
par la concaténation du nom et du prénom.

Revision ID: 0005_normalize_non_diffusible_values
Revises: 0003_contacts_confidence_check
Create Date: 2026-08-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_normalize_non_diffusible"
down_revision: str | None = "0003_contacts_confidence_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Colonnes texte alimentées par l'API et susceptibles de porter le marqueur.
TEXT_COLUMNS: tuple[str, ...] = (
    "denomination",
    "nom_complet",
    "categorie_juridique",
    "activite_principale",
    "tranche_effectifs",
    "adresse_numero",
    "adresse_type_voie",
    "adresse_libelle_voie",
    "adresse_complement",
    "code_postal",
    "commune",
    "code_commune",
    "departement",
)

#: « [ND] » comme mot isolé : couvre « [ND] » seul comme « [ND] [ND] ».
NON_DIFFUSIBLE_PATTERN = r"(^|\s)\[ND\](\s|$)"


def upgrade() -> None:
    for column in TEXT_COLUMNS:
        op.execute(
            f"UPDATE companies SET {column} = NULL WHERE {column} ~ '{NON_DIFFUSIBLE_PATTERN}'"
        )


def downgrade() -> None:
    """Sans effet : la valeur d'origine ne portait aucune information."""
