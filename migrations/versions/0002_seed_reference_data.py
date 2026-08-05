"""Données de référence : pipeline_stages (3.3) et scoring_rules (7.3).

Aucun utilisateur n'est créé ici : les comptes se créent avec
`crm users create` (lot T2).

Revision ID: 0002_seed_reference_data
Revises: 0001_initial
Create Date: 2026-08-05
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_seed_reference_data"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

pipeline_stages_table = sa.table(
    "pipeline_stages",
    sa.column("id", sa.SmallInteger),
    sa.column("key", sa.String),
    sa.column("label", sa.Text),
    sa.column("position", sa.SmallInteger),
    sa.column("is_won", sa.Boolean),
    sa.column("is_lost", sa.Boolean),
)

scoring_rules_table = sa.table(
    "scoring_rules",
    sa.column("key", sa.String),
    sa.column("label", sa.Text),
    sa.column("predicate", sa.String),
    sa.column("params", postgresql.JSONB),
    sa.column("points", sa.SmallInteger),
)

# Section 3.3 — la couleur reste à la valeur par défaut de la colonne.
PIPELINE_STAGES: list[dict[str, Any]] = [
    {
        "id": 1,
        "key": "nouveau",
        "label": "Nouveau",
        "position": 1,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 2,
        "key": "a_contacter",
        "label": "À contacter",
        "position": 2,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 3,
        "key": "email_envoye",
        "label": "Email envoyé",
        "position": 3,
        "is_won": False,
        "is_lost": False,
    },
    {"id": 4, "key": "appele", "label": "Appelé", "position": 4, "is_won": False, "is_lost": False},
    {
        "id": 5,
        "key": "relance_1",
        "label": "Relance 1",
        "position": 5,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 6,
        "key": "relance_2",
        "label": "Relance 2",
        "position": 6,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 7,
        "key": "rendez_vous",
        "label": "Rendez-vous",
        "position": 7,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 8,
        "key": "devis_envoye",
        "label": "Devis envoyé",
        "position": 8,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 9,
        "key": "negociation",
        "label": "Négociation",
        "position": 9,
        "is_won": False,
        "is_lost": False,
    },
    {
        "id": 10,
        "key": "gagne",
        "label": "Client gagné",
        "position": 10,
        "is_won": True,
        "is_lost": False,
    },
    {"id": 11, "key": "perdu", "label": "Perdu", "position": 11, "is_won": False, "is_lost": True},
]

# Section 7.3 — `predicate`, `params` et `points` sont repris à l'identique.
SCORING_RULES: list[dict[str, Any]] = [
    {
        "key": "no_website",
        "label": "Aucun site web",
        "predicate": "fact_missing",
        "params": {"field": "website_url"},
        "points": 30,
    },
    {
        "key": "weak_website",
        "label": "Site web de mauvaise qualité",
        "predicate": "fact_lt",
        "params": {"field": "website_quality_score", "value": 50},
        "points": 25,
    },
    {
        "key": "no_https",
        "label": "Site sans HTTPS",
        "predicate": "fact_equals",
        "params": {"field": "website_https", "value": False},
        "points": 10,
    },
    {
        "key": "not_responsive",
        "label": "Site non adapté au mobile",
        "predicate": "fact_equals",
        "params": {"field": "website_responsive", "value": False},
        "points": 10,
    },
    {
        "key": "very_recent",
        "label": "Immatriculation de moins de 30 jours",
        "predicate": "age_days_lt",
        "params": {"days": 30},
        "points": 20,
    },
    {
        "key": "recent",
        "label": "Immatriculation de moins de 90 jours",
        "predicate": "age_days_lt",
        "params": {"days": 90},
        "points": 10,
    },
    {
        "key": "has_email",
        "label": "Adresse email connue",
        "predicate": "has_contact",
        "params": {"channel": "email"},
        "points": 15,
    },
    {
        "key": "has_phone",
        "label": "Numéro de téléphone connu",
        "predicate": "has_contact",
        "params": {"channel": "phone"},
        "points": 10,
    },
    {
        "key": "target_sector",
        "label": "Secteur d'activité cible",
        "predicate": "naf_prefix_in",
        "params": {"prefixes": ["41", "43", "68", "69", "70", "71", "96"]},
        "points": 10,
    },
    {
        "key": "local",
        "label": "Département de proximité",
        "predicate": "departement_in",
        "params": {"codes": ["68", "67", "90"]},
        "points": 10,
    },
]


def upgrade() -> None:
    op.bulk_insert(pipeline_stages_table, PIPELINE_STAGES)
    op.bulk_insert(scoring_rules_table, SCORING_RULES)


def downgrade() -> None:
    stage_keys = tuple(stage["key"] for stage in PIPELINE_STAGES)
    rule_keys = tuple(rule["key"] for rule in SCORING_RULES)

    op.execute(scoring_rules_table.delete().where(scoring_rules_table.c.key.in_(rule_keys)))
    op.execute(pipeline_stages_table.delete().where(pipeline_stages_table.c.key.in_(stage_keys)))
