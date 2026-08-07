"""Compteur de rejets du collecteur et index des entreprises prospectables.

Revision ID: 0006_records_rejected_and_prospectable_index
Revises: 0005_normalize_non_diffusible_values
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_rejected_and_prospectable"
down_revision: str | None = "0005_normalize_non_diffusible"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collector_runs",
        sa.Column(
            "records_rejected",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Les unités non diffusibles ne sont pas prospectables (R123-232-1) et
    # représentent une part importante du référentiel : les écarter dans le
    # prédicat de l'index évite de les parcourir à chaque lecture.
    op.create_index(
        "idx_companies_prospectables",
        "companies",
        [sa.text("date_creation DESC")],
        postgresql_where=sa.text("etat_administratif = 'A' AND statut_diffusion = 'O'"),
    )


def downgrade() -> None:
    op.drop_index("idx_companies_prospectables", table_name="companies")
    op.drop_column("collector_runs", "records_rejected")
