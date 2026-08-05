"""Schéma initial — section 3 du cahier des charges.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.enums import (
    contact_channel,
    contact_origin,
    email_status,
    run_status,
    user_role,
)

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Vue de lecture de la section 3.4, reprise mot pour mot.
COMPANY_FACTS_VIEW = """
CREATE VIEW company_facts AS
SELECT DISTINCT ON (company_id, field)
       company_id, field, value, value_json, source, confidence, collected_at
FROM enrichment_facts
WHERE expires_at > now()
ORDER BY company_id, field, confidence DESC, collected_at DESC
"""


def upgrade() -> None:
    # --- 3.1 Extensions et types ---
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    op.execute("CREATE TYPE contact_channel AS ENUM ('email', 'phone')")
    op.execute("CREATE TYPE contact_origin AS ENUM ('societeinfo', 'website', 'manual', 'sirene')")
    op.execute(
        "CREATE TYPE email_status AS ENUM "
        "('draft', 'queued', 'sent', 'failed', 'bounced', 'cancelled')"
    )
    op.execute("CREATE TYPE run_status AS ENUM ('running', 'success', 'partial', 'failed')")
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'commercial', 'viewer')")

    # --- 3.8 Utilisateurs (référencés par prospects, pipeline_events, ...) ---
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column(
            "role", user_role, nullable=False, server_default=sa.text("'commercial'::user_role")
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_sessions_expiry", "sessions", ["expires_at"])

    # --- 3.2 Référentiel SIRENE ---
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("siren", sa.CHAR(length=9), nullable=False),
        sa.Column("siret_siege", sa.CHAR(length=14), nullable=False),
        sa.Column("denomination", sa.Text(), nullable=True),
        sa.Column("nom_complet", sa.Text(), nullable=True),
        sa.Column(
            "is_personne_physique",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("categorie_juridique", sa.String(length=4), nullable=True),
        sa.Column("activite_principale", sa.String(length=6), nullable=True),
        sa.Column("tranche_effectifs", sa.String(length=2), nullable=True),
        sa.Column("date_creation", sa.Date(), nullable=False),
        sa.Column("etat_administratif", sa.CHAR(length=1), nullable=False),
        sa.Column(
            "statut_diffusion",
            sa.CHAR(length=1),
            nullable=False,
            server_default=sa.text("'O'::bpchar"),
        ),
        sa.Column("adresse_numero", sa.String(length=10), nullable=True),
        sa.Column("adresse_type_voie", sa.String(length=20), nullable=True),
        sa.Column("adresse_libelle_voie", sa.Text(), nullable=True),
        sa.Column("adresse_complement", sa.Text(), nullable=True),
        sa.Column("code_postal", sa.String(length=5), nullable=True),
        sa.Column("commune", sa.Text(), nullable=True),
        sa.Column("code_commune", sa.String(length=5), nullable=True),
        sa.Column("departement", sa.String(length=3), nullable=True),
        sa.Column("date_dernier_traitement", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_synced_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "denomination IS NOT NULL OR nom_complet IS NOT NULL OR statut_diffusion = 'P'",
            name="companies_name_present",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("siren"),
    )
    op.create_index("idx_companies_date_creation", "companies", [sa.text("date_creation DESC")])
    op.create_index("idx_companies_departement", "companies", ["departement"])
    op.create_index("idx_companies_naf", "companies", ["activite_principale"])
    op.create_index(
        "idx_companies_dernier_traitement",
        "companies",
        [sa.text("date_dernier_traitement DESC")],
    )
    op.create_index(
        "idx_companies_denomination_trgm",
        "companies",
        [sa.text("coalesce(denomination, nom_complet)")],
        postgresql_using="gin",
        postgresql_ops={"coalesce(denomination, nom_complet)": "gin_trgm_ops"},
    )
    op.create_index(
        "idx_companies_actives",
        "companies",
        [sa.text("date_creation DESC")],
        postgresql_where=sa.text("etat_administratif = 'A'"),
    )

    # --- 3.3 État commercial ---
    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "color",
            sa.String(length=7),
            nullable=False,
            server_default=sa.text("'#6c757d'::character varying"),
        ),
        sa.Column("is_won", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_lost", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "prospects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_id", sa.SmallInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default=sa.text("3")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''::text")),
        sa.Column("next_action_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("estimated_value_cents", sa.BigInteger(), nullable=True),
        sa.Column("lost_reason", sa.Text(), nullable=True),
        sa.Column(
            "entered_stage_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="prospects_priority_check"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index("idx_prospects_stage", "prospects", ["stage_id"])
    op.create_index("idx_prospects_owner", "prospects", ["owner_id"])
    op.create_index(
        "idx_prospects_next_action",
        "prospects",
        ["next_action_at"],
        postgresql_where=sa.text("next_action_at IS NOT NULL"),
    )

    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("from_stage_id", sa.SmallInteger(), nullable=True),
        sa.Column("to_stage_id", sa.SmallInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["to_stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pipeline_events_prospect",
        "pipeline_events",
        ["prospect_id", sa.text("created_at DESC")],
    )

    # --- 3.4 Enrichissement ---
    op.create_table(
        "enrichment_facts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("field", sa.String(length=48), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column(
            "collected_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="enrichment_facts_confidence_check"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "field", "source", name="uq_fact"),
    )
    op.create_index("idx_facts_company", "enrichment_facts", ["company_id"])
    op.create_index("idx_facts_field", "enrichment_facts", ["field", "value"])
    op.create_index("idx_facts_expiration", "enrichment_facts", ["expires_at"])

    op.create_table(
        "enrichment_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("facts_written", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_enrichment_runs_company",
        "enrichment_runs",
        ["company_id", sa.text("started_at DESC")],
    )

    op.execute(COMPANY_FACTS_VIEW)

    # --- 3.5 Contacts et conformité ---
    op.create_table(
        "contacts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", contact_channel, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("display_value", sa.Text(), nullable=False),
        sa.Column("origin", contact_origin, nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("is_generic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "channel", "value", name="uq_contact"),
    )
    op.create_index(
        "idx_contact_primary",
        "contacts",
        ["company_id", "channel"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "opt_outs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("channel", contact_channel, nullable=False),
        sa.Column("value_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "value_hash", name="uq_optout"),
    )

    # --- 3.6 Emails et tâches ---
    op.create_table(
        "email_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "email_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=True),
        sa.Column("template_id", sa.BigInteger(), nullable=True),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            email_status,
            nullable=False,
            server_default=sa.text("'draft'::email_status"),
        ),
        sa.Column("unsubscribe_token", sa.CHAR(length=43), nullable=False),
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["template_id"], ["email_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("unsubscribe_token"),
    )
    op.create_index(
        "idx_email_queue",
        "email_messages",
        ["scheduled_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "idx_email_prospect",
        "email_messages",
        ["prospect_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("prospect_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'rappel'::character varying"),
        ),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("done_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_tasks_due", "tasks", ["due_at"], postgresql_where=sa.text("done_at IS NULL")
    )

    # --- 3.7 Scoring ---
    op.create_table(
        "scoring_rules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("predicate", sa.String(length=24), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("points", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ruleset_hash", sa.CHAR(length=16), nullable=False),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_scores_latest", "score_snapshots", ["company_id", sa.text("computed_at DESC")]
    )
    op.create_index(
        "idx_scores_ranking",
        "score_snapshots",
        [sa.text("score DESC"), sa.text("computed_at DESC")],
    )

    # --- 3.8 Collecte ---
    op.create_table(
        "collector_watermarks",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "collector_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("records_seen", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_new", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("api_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("window_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS company_facts")

    op.drop_table("collector_runs")
    op.drop_table("collector_watermarks")
    op.drop_table("score_snapshots")
    op.drop_table("scoring_rules")
    op.drop_table("tasks")
    op.drop_table("email_messages")
    op.drop_table("email_templates")
    op.drop_table("opt_outs")
    op.drop_table("contacts")
    op.drop_table("enrichment_runs")
    op.drop_table("enrichment_facts")
    op.drop_table("pipeline_events")
    op.drop_table("prospects")
    op.drop_table("pipeline_stages")
    op.drop_table("companies")
    op.drop_table("sessions")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS user_role")
    op.execute("DROP TYPE IF EXISTS run_status")
    op.execute("DROP TYPE IF EXISTS email_status")
    op.execute("DROP TYPE IF EXISTS contact_origin")
    op.execute("DROP TYPE IF EXISTS contact_channel")

    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
