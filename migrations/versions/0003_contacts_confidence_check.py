"""Contrainte de bornes sur contacts.confidence.

Aligne `contacts.confidence` sur `enrichment_facts.confidence` : les deux
colonnes portent une probabilité, elles sont bornées de la même façon.

Revision ID: 0003_contacts_confidence_check
Revises: 0002_seed_reference_data
Create Date: 2026-08-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_contacts_confidence_check"
down_revision: str | None = "0002_seed_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "contacts_confidence_check"


def upgrade() -> None:
    op.create_check_constraint(CONSTRAINT_NAME, "contacts", "confidence BETWEEN 0 AND 1")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "contacts", type_="check")
