"""Schéma de la section 3 appliqué sur un PostgreSQL réel."""

from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

NOW = datetime.now(tz=UTC)

INSERT_COMPANY = text(
    """
    INSERT INTO companies (
        siren, siret_siege, denomination, nom_complet,
        date_creation, etat_administratif, statut_diffusion
    )
    VALUES (
        :siren, :siret, :denomination, :nom_complet,
        :date_creation, 'A', :statut_diffusion
    )
    RETURNING id
    """
)

INSERT_FACT = text(
    """
    INSERT INTO enrichment_facts (
        company_id, field, value, source, confidence, collected_at, expires_at
    )
    VALUES (:company_id, :field, :value, :source, :confidence, :collected_at, :expires_at)
    """
)

INSERT_CONTACT = text(
    """
    INSERT INTO contacts (
        company_id, channel, value, display_value, origin, confidence, is_primary
    )
    VALUES (:company_id, :channel, :value, :value, 'manual', 0.90, :is_primary)
    """
)


def _insert_company(
    connection: Connection,
    *,
    siren: str,
    denomination: str | None = "IOSYS SAS",
    nom_complet: str | None = None,
    statut_diffusion: str = "O",
) -> int:
    """Insère une entreprise minimale et renvoie son identifiant."""
    result = connection.execute(
        INSERT_COMPANY,
        {
            "siren": siren,
            "siret": siren + "00015",
            "denomination": denomination,
            "nom_complet": nom_complet,
            "date_creation": date(2026, 1, 15),
            "statut_diffusion": statut_diffusion,
        },
    )
    return int(result.scalar_one())


def test_migration_cycle_is_reversible(alembic_config: Config, migrated_engine: Engine) -> None:
    """`upgrade head` -> `downgrade base` -> `upgrade head` doit fonctionner."""
    migrated_engine.dispose()
    command.downgrade(alembic_config, "base")

    with migrated_engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'companies'")
        ).scalar_one()
    assert remaining == 0

    command.upgrade(alembic_config, "head")

    with migrated_engine.connect() as conn:
        tables = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'companies'")
        ).scalar_one()
        view = conn.execute(
            text("SELECT count(*) FROM information_schema.views WHERE table_name = 'company_facts'")
        ).scalar_one()
    assert tables == 1
    assert view == 1


def test_reference_data_is_seeded(connection: Connection) -> None:
    """Les 11 étapes de 3.3 et les 10 règles de 7.3 sont présentes."""
    stages = connection.execute(
        text("SELECT id, key, label, position, is_won, is_lost FROM pipeline_stages ORDER BY id")
    ).all()
    assert len(stages) == 11
    assert [stage.key for stage in stages] == [
        "nouveau",
        "a_contacter",
        "email_envoye",
        "appele",
        "relance_1",
        "relance_2",
        "rendez_vous",
        "devis_envoye",
        "negociation",
        "gagne",
        "perdu",
    ]
    assert [stage.id for stage in stages] == list(range(1, 12))
    assert [stage.position for stage in stages] == list(range(1, 12))
    won = [stage.key for stage in stages if stage.is_won]
    lost = [stage.key for stage in stages if stage.is_lost]
    assert won == ["gagne"]
    assert lost == ["perdu"]

    rules = connection.execute(
        text("SELECT key, predicate, params, points FROM scoring_rules ORDER BY id")
    ).all()
    # 10 règles seedées en 0002, plus `never_enriched` ajoutée par 0007.
    assert len(rules) == 11
    assert {rule.key: rule.points for rule in rules} == {
        "no_website": 30,
        "never_enriched": 5,
        "weak_website": 25,
        "no_https": 10,
        "not_responsive": 10,
        "very_recent": 10,
        "recent": 10,
        "has_email": 15,
        "has_phone": 10,
        "target_sector": 10,
        "local": 10,
    }
    by_key = {rule.key: rule for rule in rules}
    # Recalibrage 0007 : absence CONSTATÉE contre absence d'analyse.
    assert by_key["no_website"].predicate == "fact_equals"
    assert by_key["no_website"].params == {"field": "has_website", "value": False}
    assert by_key["never_enriched"].predicate == "fact_missing"
    assert by_key["never_enriched"].params == {"field": "has_website"}
    assert by_key["weak_website"].params == {"field": "website_quality_score", "value": 50}
    # 0008 : `local` est mise en sommeil, et son paramètre corrigé (88 manquant).
    assert by_key["local"].params == {"codes": ["68", "67", "90", "88"]}


def test_companies_name_present_rejects_nameless_public_unit(connection: Connection) -> None:
    """Une unité diffusable sans dénomination ni nom complet est refusée."""
    with pytest.raises(IntegrityError, match="companies_name_present"):
        _insert_company(connection, siren="111111111", denomination=None, statut_diffusion="O")


def test_companies_name_present_accepts_partial_diffusion(connection: Connection) -> None:
    """Une unité en diffusion partielle est stockée telle quelle (section 5.3)."""
    company_id = _insert_company(
        connection, siren="222222222", denomination=None, statut_diffusion="P"
    )

    assert company_id > 0


def test_uq_fact_rejects_duplicate_source(connection: Connection) -> None:
    """Un même (entreprise, champ, source) ne peut exister qu'une fois."""
    company_id = _insert_company(connection, siren="333333333")
    params = {
        "company_id": company_id,
        "field": "website_url",
        "value": "https://exemple.fr",
        "source": "societeinfo",
        "confidence": 0.9,
        "collected_at": NOW,
        "expires_at": NOW + timedelta(days=90),
    }
    connection.execute(INSERT_FACT, params)

    with pytest.raises(IntegrityError, match="uq_fact"):
        connection.execute(INSERT_FACT, {**params, "value": "https://autre.fr"})


def test_idx_contact_primary_allows_one_primary_per_channel(connection: Connection) -> None:
    """Deux contacts primaires du même canal pour une entreprise sont refusés."""
    company_id = _insert_company(connection, siren="444444444")
    connection.execute(
        INSERT_CONTACT,
        {
            "company_id": company_id,
            "channel": "email",
            "value": "contact@exemple.fr",
            "is_primary": True,
        },
    )

    # Un second contact non primaire du même canal reste autorisé.
    connection.execute(
        INSERT_CONTACT,
        {
            "company_id": company_id,
            "channel": "email",
            "value": "info@exemple.fr",
            "is_primary": False,
        },
    )

    with pytest.raises(IntegrityError, match="idx_contact_primary"):
        connection.execute(
            INSERT_CONTACT,
            {
                "company_id": company_id,
                "channel": "email",
                "value": "commercial@exemple.fr",
                "is_primary": True,
            },
        )


def test_contacts_confidence_is_bounded(connection: Connection) -> None:
    """`contacts.confidence` est bornée à [0, 1], comme celle des faits."""
    company_id = _insert_company(connection, siren="666666666")
    statement = text(
        """
        INSERT INTO contacts (
            company_id, channel, value, display_value, origin, confidence
        )
        VALUES (:company_id, 'email', :value, :value, 'manual', :confidence)
        """
    )

    with pytest.raises(IntegrityError, match="contacts_confidence_check"):
        connection.execute(
            statement,
            {
                "company_id": company_id,
                "value": "hors-bornes@exemple.fr",
                "confidence": 1.5,
            },
        )


def test_company_facts_keeps_highest_confidence_and_drops_expired(
    connection: Connection,
) -> None:
    """La vue retient la confiance la plus haute et ignore les faits expirés."""
    company_id = _insert_company(connection, siren="555555555")

    # Récent mais peu fiable.
    connection.execute(
        INSERT_FACT,
        {
            "company_id": company_id,
            "field": "website_url",
            "value": "https://douteux.fr",
            "source": "contact_extract",
            "confidence": 0.60,
            "collected_at": NOW,
            "expires_at": NOW + timedelta(days=90),
        },
    )
    # Plus ancien mais fiable : c'est celui-ci qui doit ressortir.
    connection.execute(
        INSERT_FACT,
        {
            "company_id": company_id,
            "field": "website_url",
            "value": "https://fiable.fr",
            "source": "societeinfo",
            "confidence": 0.90,
            "collected_at": NOW - timedelta(days=10),
            "expires_at": NOW + timedelta(days=80),
        },
    )
    # Confiance maximale, mais expiré : exclu de la vue.
    connection.execute(
        INSERT_FACT,
        {
            "company_id": company_id,
            "field": "website_cms",
            "value": "wordpress",
            "source": "website_probe",
            "confidence": 0.95,
            "collected_at": NOW - timedelta(days=200),
            "expires_at": NOW - timedelta(days=110),
        },
    )

    rows = connection.execute(
        text(
            "SELECT field, value, source, confidence FROM company_facts "
            "WHERE company_id = :company_id ORDER BY field"
        ),
        {"company_id": company_id},
    ).all()

    assert [(row.field, row.value) for row in rows] == [("website_url", "https://fiable.fr")]
    assert rows[0].source == "societeinfo"
