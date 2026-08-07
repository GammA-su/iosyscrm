"""Désactive les règles qui se déclenchent sur tout le portefeuille.

Sur les 635 premières entreprises collectées, `local`, `recent` et
`very_recent` se déclenchaient ensemble sur la quasi-totalité des lignes :
la collecte est déjà restreinte aux départements 67/68/88/90 et aux
créations de moins de 30 jours. Ces règles décalaient la moyenne sans jamais
départager deux fiches.

Désactivation plutôt que suppression : elles retrouveront leur sens si
`SIRENE_DEPARTEMENTS` s'élargit ou si `SIRENE_LOOKBACK_DAYS` augmente.

Revision ID: 0008_drop_undiscriminating
Revises: 0007_recalibrate_no_website
Create Date: 2026-08-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_drop_undiscriminating"
down_revision: str | None = "0007_recalibrate_no_website"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `local` omettait le 88, pourtant collecté : le paramètre est corrigé
    # avant la mise en sommeil, pour que la règle soit juste à sa réactivation.
    op.execute(
        """
        UPDATE scoring_rules
        SET is_active = false,
            params = '{"codes": ["68", "67", "90", "88"]}'::jsonb,
            updated_at = now()
        WHERE key = 'local'
        """
    )
    op.execute(
        """
        UPDATE scoring_rules
        SET points = 10, updated_at = now()
        WHERE key = 'very_recent'
        """
    )
    op.execute(
        """
        UPDATE scoring_rules
        SET is_active = false, updated_at = now()
        WHERE key = 'recent'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE scoring_rules
        SET is_active = true,
            params = '{"codes": ["68", "67", "90"]}'::jsonb,
            updated_at = now()
        WHERE key = 'local'
        """
    )
    op.execute(
        """
        UPDATE scoring_rules
        SET points = 20, updated_at = now()
        WHERE key = 'very_recent'
        """
    )
    op.execute(
        """
        UPDATE scoring_rules
        SET is_active = true, updated_at = now()
        WHERE key = 'recent'
        """
    )
