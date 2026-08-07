"""Scoring sur PostgreSQL réel — section 7.

Les règles utilisées sont celles réellement présentes en base : seed de la
migration 0002 (tableau 7.3), recalibré par 0007 (`no_website` contre
`never_enriched`) puis par 0008 (mise en sommeil des règles qui se
déclenchent sur tout le portefeuille).
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.enrichment import EnrichmentFact
from app.models.scoring import ScoreSnapshot, ScoringRule
from app.repositories import scoring as scoring_repo
from app.services.scoring import load_rules, rescore_all, rescore_company
from app.services.scoring.engine import ruleset_hash
from app.services.scoring.rules import UnknownPredicateError

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

#: Entreprise volontairement neutre : ni récente, ni locale, ni secteur cible.
NEUTRAL_NAF = "62.01Z"
NEUTRAL_DEPARTEMENT = "75"


def _company(
    db: Session,
    *,
    siren: str,
    date_creation: date = date(2020, 1, 1),
    activite: str | None = NEUTRAL_NAF,
    departement: str | None = NEUTRAL_DEPARTEMENT,
) -> Company:
    company = Company(
        siren=siren,
        siret_siege=f"{siren}00015",
        denomination=f"ENTREPRISE {siren}",
        date_creation=date_creation,
        etat_administratif="A",
        statut_diffusion="O",
        activite_principale=activite,
        departement=departement,
    )
    db.add(company)
    db.commit()
    return company


def _fact(
    db: Session,
    company: Company,
    *,
    field: str,
    value: str | None = None,
    value_json: Any = None,
    source: str = "societeinfo",
    confidence: float = 0.90,
    expires_in_days: int = 90,
) -> None:
    db.add(
        EnrichmentFact(
            company_id=company.id,
            field=field,
            value=value,
            value_json=value_json,
            source=source,
            confidence=confidence,
            collected_at=NOW,
            expires_at=NOW + timedelta(days=expires_in_days),
        )
    )
    db.commit()


#: Instructions de contrôle de transaction. Elles n'existent que parce que les
#: tests s'exécutent dans une transaction annulée en fin de test : en
#: production, `commit()` ne passe pas par un curseur.
TRANSACTION_CONTROL = ("SAVEPOINT", "RELEASE", "ROLLBACK", "COMMIT", "BEGIN")


class QueryCounter:
    """Compte les requêtes SQL réellement envoyées à PostgreSQL."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.statements: list[str] = []

    def _record(self, *args: Any, **kwargs: Any) -> None:
        statement = str(args[2]).split("\n", 1)[0]
        if not statement.upper().startswith(TRANSACTION_CONTROL):
            self.statements.append(statement)

    def __enter__(self) -> "QueryCounter":
        event.listen(self._connection, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(self._connection, "before_cursor_execute", self._record)

    def __len__(self) -> int:
        return len(self.statements)


# --- Absence de site contre absence d'analyse ---------------------------


def test_a_company_never_analysed_scores_five(db_session: Session) -> None:
    """Aucun fait : la fiche entre dans la file d'attente, pas en tête."""
    company = _company(db_session, siren="930000001")

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert result.breakdown == {"never_enriched": 5}
    assert result.score == 5
    assert result.has_enrichment_data is False


def test_a_confirmed_absence_of_website_scores_thirty(db_session: Session) -> None:
    """`has_website = false` : absence CONSTATÉE, le meilleur signal."""
    company = _company(db_session, siren="930000002")
    _fact(db_session, company, field="has_website", value_json=False)

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert result.breakdown == {"no_website": 30}
    assert result.score == 30
    assert result.has_enrichment_data is True


def test_a_company_with_a_website_triggers_neither_rule(db_session: Session) -> None:
    company = _company(db_session, siren="930000003")
    _fact(db_session, company, field="has_website", value_json=True)
    _fact(db_session, company, field="website_url", value="https://exemple.fr")

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert result.breakdown == {}
    assert result.score == 0


def test_an_expired_fact_falls_back_to_never_enriched(db_session: Session) -> None:
    """Un fait périmé est une absence de connaissance, pas une absence de site.

    `company_facts` écarte les faits expirés : `has_website` redevient absent,
    donc c'est `never_enriched` qui se déclenche, jamais `no_website`.
    """
    company = _company(db_session, siren="930000004")
    _fact(db_session, company, field="has_website", value_json=False, expires_in_days=-1)

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert result.breakdown == {"never_enriched": 5}
    assert "no_website" not in result.breakdown
    assert result.has_enrichment_data is False


def test_a_scored_company_never_carries_both_absence_rules(db_session: Session) -> None:
    """Les deux règles s'excluent : `has_website` est présent ou absent."""
    never_analysed = _company(db_session, siren="930000005")
    without_site = _company(db_session, siren="930000006")
    _fact(db_session, without_site, field="has_website", value_json=False)

    for company in (never_analysed, without_site):
        result = rescore_company(db_session, company.id)
        assert result is not None
        assert not {"no_website", "never_enriched"} <= set(result.breakdown)


# --- Contexte lu depuis la base ----------------------------------------


def test_the_context_reads_facts_contacts_and_company_columns(db_session: Session) -> None:
    company = _company(
        db_session,
        siren="930000010",
        date_creation=NOW.date() - timedelta(days=10),
        activite="43.32A",
        departement="68",
    )
    _fact(db_session, company, field="has_website", value_json=True)
    _fact(db_session, company, field="website_url", value="https://exemple.fr")
    _fact(db_session, company, field="website_quality_score", value="35", value_json=35)
    _fact(db_session, company, field="website_https", value_json=False)
    _fact(db_session, company, field="website_responsive", value_json=False)
    db_session.add_all(
        [
            Contact(
                company_id=company.id,
                channel="email",
                value="contact@exemple.fr",
                display_value="contact@exemple.fr",
                origin="societeinfo",
                confidence=0.90,
            ),
            Contact(
                company_id=company.id,
                channel="phone",
                value="+33388123456",
                display_value="03 88 12 34 56",
                origin="societeinfo",
                confidence=0.90,
            ),
        ]
    )
    db_session.commit()

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert result.breakdown == {
        "weak_website": 25,
        "no_https": 10,
        "not_responsive": 10,
        "very_recent": 10,
        "has_email": 15,
        "has_phone": 10,
        "target_sector": 10,
    }
    assert result.score == 90


def test_the_most_reliable_fact_wins(db_session: Session) -> None:
    """Le contexte est bâti sur `company_facts` : confiance d'abord."""
    company = _company(db_session, siren="930000011")
    _fact(
        db_session,
        company,
        field="website_quality_score",
        value_json=80,
        source="website_probe",
        confidence=0.95,
    )
    _fact(
        db_session,
        company,
        field="website_quality_score",
        value_json=20,
        source="contact_extract",
        confidence=0.60,
    )

    result = rescore_company(db_session, company.id)

    assert result is not None
    assert "weak_website" not in result.breakdown
    assert result.has_enrichment_data is True


# --- Instantanés --------------------------------------------------------


def test_rescore_company_writes_a_snapshot(db_session: Session) -> None:
    company = _company(db_session, siren="930000020")

    result = rescore_company(db_session, company.id)
    db_session.commit()

    assert result is not None
    snapshots = scoring_repo.latest_scores(db_session, [company.id])
    assert len(snapshots) == 1
    assert snapshots[0].score == 5
    assert snapshots[0].breakdown == {"never_enriched": 5}
    assert len(snapshots[0].ruleset_hash) == 16


def test_latest_scores_returns_only_the_most_recent(db_session: Session) -> None:
    company = _company(db_session, siren="930000021")
    rescore_company(db_session, company.id)
    db_session.commit()
    db_session.execute(
        insert(ScoreSnapshot).values(
            company_id=company.id,
            score=77,
            breakdown={"local": 10},
            ruleset_hash="0123456789abcdef",
            computed_at=NOW + timedelta(days=1),
        )
    )
    db_session.commit()

    snapshots = scoring_repo.latest_scores(db_session, [company.id])

    assert len(snapshots) == 1
    assert snapshots[0].score == 77


def test_rescore_company_returns_none_for_an_unknown_company(db_session: Session) -> None:
    assert rescore_company(db_session, 99_999_999) is None


# --- Jeu de règles ------------------------------------------------------


def test_deactivating_a_rule_changes_the_ruleset_hash(db_session: Session) -> None:
    before = ruleset_hash(load_rules(db_session))

    db_session.execute(text("UPDATE scoring_rules SET is_active = false WHERE key = 'has_email'"))
    db_session.commit()

    after = ruleset_hash(load_rules(db_session))
    assert after != before
    assert "has_email" not in {rule.key for rule in load_rules(db_session)}


def test_rules_deactivated_by_migration_are_out_of_the_active_set(db_session: Session) -> None:
    """0008 met `local` et `recent` en sommeil : elles ne scorent plus.

    Elles restent en base, réactivables si la collecte s'élargit.
    """
    active = {rule.key for rule in load_rules(db_session)}

    assert "local" not in active
    assert "recent" not in active
    stored = db_session.execute(
        text("SELECT key FROM scoring_rules WHERE is_active = false ORDER BY key")
    ).scalars()
    assert list(stored) == ["local", "recent"]


def test_an_unknown_predicate_in_the_database_raises(db_session: Session) -> None:
    """Une règle inexploitable arrête le chargement, elle n'est pas ignorée."""
    db_session.add(
        ScoringRule(
            key="exotique",
            label="Règle exotique",
            predicate="fact_between",
            params={"field": "website_ttfb_ms"},
            points=5,
        )
    )
    db_session.commit()

    with pytest.raises(UnknownPredicateError, match="fact_between"):
        load_rules(db_session)

    with pytest.raises(UnknownPredicateError):
        rescore_all(db_session)


# --- Volume -------------------------------------------------------------


@pytest.fixture
def five_thousand_companies(db_session: Session) -> Iterator[int]:
    """5 000 entreprises insérées en une instruction."""
    rows = [
        {
            "siren": f"{940_000_000 + index:09d}",
            "siret_siege": f"{940_000_000 + index:09d}00015",
            "denomination": f"ENTREPRISE {index}",
            "date_creation": date(2020, 1, 1),
            "etat_administratif": "A",
            "statut_diffusion": "O",
            "activite_principale": NEUTRAL_NAF,
            "departement": NEUTRAL_DEPARTEMENT,
        }
        for index in range(5000)
    ]
    db_session.execute(insert(Company), rows)
    db_session.commit()
    yield 5000


def test_rescore_all_does_not_query_per_company(
    db_session: Session, five_thousand_companies: int
) -> None:
    """Le piège classique : une requête par entreprise.

    Budget de la section 7 : (5000 / 1000) fois 3 = 15 instructions au plus.
    """
    connection = db_session.connection()

    with QueryCounter(connection) as counter:
        summary = rescore_all(db_session, batch_size=1000)

    assert summary.companies == five_thousand_companies
    assert len(counter) <= 15, "\n".join(counter.statements)
    # Aucune instruction ne doit citer une seule entreprise.
    assert not any("id = " in statement for statement in counter.statements)

    written = db_session.execute(select(text("count(*)")).select_from(ScoreSnapshot)).scalar_one()
    assert written == five_thousand_companies


def test_rescore_all_writes_one_snapshot_per_company(db_session: Session) -> None:
    for index in range(3):
        _company(db_session, siren=f"95000000{index}")

    summary = rescore_all(db_session, batch_size=2)

    assert summary.companies == 3
    snapshots = db_session.execute(select(ScoreSnapshot)).scalars().all()
    assert len(snapshots) == 3
    assert {snapshot.ruleset_hash for snapshot in snapshots} == {summary.ruleset_hash}
    assert all(snapshot.score == 5 for snapshot in snapshots)


def test_rescore_all_rejects_a_non_positive_batch_size(db_session: Session) -> None:
    with pytest.raises(ValueError, match="strictement positive"):
        rescore_all(db_session, batch_size=0)
