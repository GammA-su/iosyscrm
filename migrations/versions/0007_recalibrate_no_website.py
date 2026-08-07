"""Sépare « pas de site » de « jamais analysée » dans le jeu de règles.

`fact_missing` sur `website_url` confondait deux états commercialement
opposés : une entreprise dont on a constaté qu'elle n'a pas de site, et une
entreprise qu'on n'a pas encore regardée. La première est le meilleur signal
du portefeuille, la seconde n'est qu'une fiche en attente de traitement.

`no_website` s'appuie désormais sur l'absence CONSTATÉE (`has_website` à
`false`), et une règle distincte `never_enriched` fait remonter les fiches
sans aucune donnée, sans les placer devant un prospect qualifié.

Revision ID: 0007_recalibrate_no_website
Revises: 0006_rejected_and_prospectable
Create Date: 2026-08-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_recalibrate_no_website"
down_revision: str | None = "0006_rejected_and_prospectable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEVER_ENRICHED_KEY = "never_enriched"


def upgrade() -> None:
    op.execute(
        """
        UPDATE scoring_rules
        SET predicate = 'fact_equals',
            params = '{"field": "has_website", "value": false}'::jsonb,
            updated_at = now()
        WHERE key = 'no_website'
        """
    )
    op.execute(
        """
        INSERT INTO scoring_rules (key, label, predicate, params, points, is_active)
        VALUES (
            'never_enriched',
            'Jamais analysée',
            'fact_missing',
            '{"field": "has_website"}'::jsonb,
            5,
            true
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM scoring_rules WHERE key = 'never_enriched'")
    op.execute(
        """
        UPDATE scoring_rules
        SET predicate = 'fact_missing',
            params = '{"field": "website_url"}'::jsonb,
            updated_at = now()
        WHERE key = 'no_website'
        """
    )
