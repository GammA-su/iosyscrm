"""Orchestration de l'enrichissement sur PostgreSQL réel — sections 6.1 et 6.6."""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.company import Company
from app.models.enrichment import EnrichmentFact
from app.repositories import contact as contact_repo
from app.repositories import enrichment as enrichment_repo
from app.repositories import scoring as scoring_repo
from app.services.enrichment.fields import EnrichmentField
from app.services.enrichment.orchestrator import (
    EnrichmentOrchestrator,
    default_provider_factory,
)
from app.services.enrichment.providers.base import (
    ContactCandidate,
    EnrichmentProvider,
    FactCandidate,
    ProviderContext,
)
from app.services.enrichment.providers.contact_extract import ContactExtractProvider
from app.services.enrichment.providers.societeinfo import SocieteInfoProvider

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENRICHMENT_ENABLED": True,
        "ENRICHMENT_TTL_DAYS": 90,
        "ENRICHMENT_BATCH_SIZE": 50,
        "ENRICHMENT_MAX_WORKERS": 4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _company(
    db: Session,
    *,
    siren: str,
    statut_diffusion: str = "O",
    etat_administratif: str = "A",
) -> Company:
    company = Company(
        siren=siren,
        siret_siege=f"{siren}00015",
        denomination=f"ENTREPRISE {siren}",
        date_creation=date(2026, 7, 1),
        etat_administratif=etat_administratif,
        statut_diffusion=statut_diffusion,
    )
    db.add(company)
    db.commit()
    return company


class StubProvider:
    """Fournisseur déterministe, sans réseau."""

    def __init__(
        self,
        name: str,
        confidence: float,
        *,
        facts: Sequence[FactCandidate] = (),
        contacts: Sequence[ContactCandidate] = (),
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.confidence = confidence
        self._facts = list(facts)
        self._contacts = list(contacts)
        self._raises = raises
        self.calls = 0

    def run(self, company: Company, context: ProviderContext) -> list[FactCandidate]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        context.contacts.extend(self._contacts)
        return list(self._facts)


def _orchestrator(
    providers: Sequence[EnrichmentProvider],
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: Callable[[], datetime] = lambda: NOW,
) -> EnrichmentOrchestrator:
    return EnrichmentOrchestrator(
        settings or _settings(),
        provider_factory=lambda: providers,
        session_factory=session_factory,
        now=now,
    )


# --- Isolation des fournisseurs ----------------------------------------


def test_a_failing_provider_does_not_stop_the_next_one(db_session: Session) -> None:
    """Chaque fournisseur est isolé : l'échec est tracé, la suite s'exécute."""
    company = _company(db_session, siren="920000001")
    failing = StubProvider("societeinfo", 0.90, raises=RuntimeError("API indisponible"))
    following = StubProvider(
        "website_probe",
        0.95,
        facts=[FactCandidate(field=EnrichmentField.WEBSITE_QUALITY_SCORE, value_json=72)],
    )

    runs = _orchestrator([failing, following]).enrich_company(db_session, company)

    assert following.calls == 1
    assert [(run.provider, run.status) for run in runs] == [
        ("societeinfo", "failed"),
        ("website_probe", "success"),
    ]
    assert runs[0].error is not None
    assert "API indisponible" in runs[0].error
    assert runs[1].facts_written == 1

    facts = enrichment_repo.list_facts(db_session, company.id)
    assert [fact.field for fact in facts] == [EnrichmentField.WEBSITE_QUALITY_SCORE]


def test_an_unknown_field_is_rejected(db_session: Session) -> None:
    """Le vocabulaire de la section 3.4 est fermé, sans contrainte SQL."""
    company = _company(db_session, siren="920000002")
    provider = StubProvider(
        "societeinfo", 0.90, facts=[FactCandidate(field="website_colour", value="bleu")]
    )

    runs = _orchestrator([provider]).enrich_company(db_session, company)

    assert runs[0].status == "failed"
    assert runs[0].error is not None
    assert "website_colour" in runs[0].error
    assert enrichment_repo.list_facts(db_session, company.id) == []


def test_write_fact_raises_on_an_unknown_field(db_session: Session) -> None:
    company = _company(db_session, siren="920000003")

    with pytest.raises(ValueError, match="website_colour"):
        _orchestrator([]).write_fact(
            db_session,
            company_id=company.id,
            candidate=FactCandidate(field="website_colour"),
            source="societeinfo",
            confidence=0.9,
        )


# --- Confiance et vue company_facts ------------------------------------


def test_a_recent_low_confidence_fact_does_not_mask_a_reliable_one(
    db_session: Session,
) -> None:
    """La vue `company_facts` départage par confiance avant la fraîcheur."""
    company = _company(db_session, siren="920000010")
    reliable = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.WEBSITE_URL, value="https://fiable.fr")],
    )
    orchestrator = _orchestrator([reliable])
    orchestrator.enrich_company(db_session, company)

    # Plus tard, une source moins fiable propose une autre valeur.
    doubtful = StubProvider(
        "contact_extract",
        0.60,
        facts=[FactCandidate(field=EnrichmentField.WEBSITE_URL, value="https://douteux.fr")],
    )
    _orchestrator([doubtful], now=lambda: NOW + timedelta(days=1)).enrich_company(
        db_session, company
    )

    rows = db_session.execute(
        text(
            "SELECT value, source, confidence FROM company_facts "
            "WHERE company_id = :cid AND field = 'website_url'"
        ),
        {"cid": company.id},
    ).all()

    assert len(rows) == 1
    assert rows[0].value == "https://fiable.fr"
    assert rows[0].source == "societeinfo"


def test_facts_expire_after_the_configured_ttl(db_session: Session) -> None:
    company = _company(db_session, siren="920000011")
    provider = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.HAS_WEBSITE, value_json=True)],
    )

    _orchestrator([provider], settings=_settings(ENRICHMENT_TTL_DAYS=30)).enrich_company(
        db_session, company
    )

    fact = enrichment_repo.list_facts(db_session, company.id)[0]
    assert fact.expires_at == NOW + timedelta(days=30)
    assert fact.collected_at == NOW


def test_facts_are_upserted_on_company_field_source(db_session: Session) -> None:
    """Deux passages du même fournisseur ne créent pas de doublon."""
    company = _company(db_session, siren="920000012")
    provider = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.EFFECTIF_ESTIME, value="3", value_json=3)],
    )
    orchestrator = _orchestrator([provider])

    orchestrator.enrich_company(db_session, company)
    orchestrator.enrich_company(db_session, company)

    facts = enrichment_repo.list_facts(db_session, company.id)
    assert len(facts) == 1
    assert facts[0].value == "3"


# --- Contacts -----------------------------------------------------------


def test_contacts_are_persisted_and_never_downgraded(db_session: Session) -> None:
    company = _company(db_session, siren="920000020")
    reliable = StubProvider(
        "societeinfo",
        0.90,
        contacts=[
            ContactCandidate(
                channel="email",
                value="contact@exemple.fr",
                display_value="Contact@Exemple.fr",
                origin="societeinfo",
                confidence=0.90,
                is_generic=True,
            )
        ],
    )
    _orchestrator([reliable]).enrich_company(db_session, company)

    doubtful = StubProvider(
        "contact_extract",
        0.60,
        contacts=[
            ContactCandidate(
                channel="email",
                value="contact@exemple.fr",
                display_value="contact@exemple.fr",
                origin="website",
                confidence=0.60,
            )
        ],
    )
    _orchestrator([doubtful]).enrich_company(db_session, company)

    contacts = contact_repo.list_for_company(db_session, company.id)
    assert len(contacts) == 1
    assert contacts[0].origin == "societeinfo"
    assert float(contacts[0].confidence) == pytest.approx(0.90)
    assert contacts[0].is_generic is True


def test_contact_extract_runs_even_when_contacts_are_already_known(
    db_session: Session,
) -> None:
    """Aucun fournisseur n'est sauté : un contact connu n'est pas un verrou.

    Sans cela, un contact societeinfo obsolète ou saisi à la main
    empêcherait définitivement toute extraction depuis le site.
    """
    company = _company(db_session, siren="920000021")
    existing = ContactCandidate(
        channel="email",
        value="contact@exemple.fr",
        display_value="contact@exemple.fr",
        origin="societeinfo",
        confidence=0.90,
    )
    _orchestrator([StubProvider("societeinfo", 0.90, contacts=[existing])]).enrich_company(
        db_session, company
    )
    assert len(contact_repo.list_for_company(db_session, company.id)) == 1

    extractor = StubProvider(
        ContactExtractProvider.name,
        0.60,
        contacts=[
            ContactCandidate(
                channel="email",
                value="accueil@exemple.fr",
                display_value="accueil@exemple.fr",
                origin="website",
                confidence=0.60,
                is_generic=True,
            )
        ],
    )
    runs = _orchestrator([extractor]).enrich_company(db_session, company)

    assert extractor.calls == 1
    assert [(run.provider, run.status) for run in runs] == [("contact_extract", "success")]

    contacts = {
        contact.value: contact for contact in contact_repo.list_for_company(db_session, company.id)
    }
    assert set(contacts) == {"contact@exemple.fr", "accueil@exemple.fr"}
    # Le contact préexistant garde sa source et sa confiance.
    assert contacts["contact@exemple.fr"].origin == "societeinfo"
    assert float(contacts["contact@exemple.fr"].confidence) == pytest.approx(0.90)
    assert contacts["accueil@exemple.fr"].origin == "website"


def test_no_societeinfo_personal_data_ever_reaches_the_database(
    db_session: Session,
    load_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """Section 8 : bénéficiaires effectifs et dirigeants ne sont pas stockés.

    Le fournisseur réel est exécuté de bout en bout, puis TOUTES les colonnes
    des tables écrites sont converties en texte et fouillées.
    """
    company = _company(db_session, siren="920000025")
    base_url = "https://societeinfo.test/app/rest/api/v2"
    settings = _settings(
        SOCIETEINFO_ENABLED=True,
        SOCIETEINFO_API_KEY="cle-test",
        SOCIETEINFO_BASE_URL=base_url,
    )
    payload = load_fixture("societeinfo/entreprise.json")
    assert payload["organization"]["beneficiaires_effectifs"]
    assert payload["organization"]["contacts"]["main_corporate_officier"]["birth_date"]

    with respx.mock:
        respx.get(f"{base_url}/company.json/{company.siren}").mock(
            return_value=httpx.Response(200, json=payload)
        )
        runs = EnrichmentOrchestrator(
            settings,
            provider_factory=lambda: (SocieteInfoProvider(settings),),
            now=lambda: NOW,
        ).enrich_company(db_session, company)

    assert [run.status for run in runs] == ["success"]

    stored = " ".join(
        db_session.execute(text(f"SELECT coalesce(string_agg(t::text, ' '), '') FROM {table} t"))
        .scalar_one()
        .lower()
        for table in ("enrichment_facts", "contacts", "companies")
    )
    for forbidden in (
        "1978-04-12",
        "1982-11-03",
        "durand",
        "pourcentage_parts",
        "details_parts_indirectes",
        "details_votes_indirects",
        "beneficiaires_effectifs",
        "corporate_officier",
        "perso-exemple.fr",
        "présidente",
    ):
        assert forbidden not in stored, f"{forbidden!r} a été stocké"

    # Ce qui est légitime a bien été écrit.
    contacts = {c.value: c for c in contact_repo.list_for_company(db_session, company.id)}
    assert contacts["claire.martin@strasbourg-immobilier-conseil.fr"].is_primary is True
    assert contacts["contact@strasbourg-immobilier-conseil.fr"].is_primary is False
    fields = {fact.field for fact in enrichment_repo.list_facts(db_session, company.id)}
    assert EnrichmentField.DIRIGEANT_PRESENT in fields


def test_a_new_primary_contact_demotes_the_previous_one(db_session: Session) -> None:
    """`idx_contact_primary` n'admet qu'un contact principal par canal."""
    company = _company(db_session, siren="920000026")
    first = StubProvider(
        "societeinfo",
        0.90,
        contacts=[
            ContactCandidate(
                channel="email",
                value="ancien@exemple.fr",
                display_value="ancien@exemple.fr",
                origin="societeinfo",
                confidence=0.90,
                is_primary=True,
            )
        ],
    )
    _orchestrator([first]).enrich_company(db_session, company)

    second = StubProvider(
        "societeinfo",
        0.90,
        contacts=[
            ContactCandidate(
                channel="email",
                value="nouveau@exemple.fr",
                display_value="nouveau@exemple.fr",
                origin="societeinfo",
                confidence=0.90,
                is_primary=True,
            )
        ],
    )
    _orchestrator([second]).enrich_company(db_session, company)

    contacts = {
        c.value: c.is_primary for c in contact_repo.list_for_company(db_session, company.id)
    }
    assert contacts == {"ancien@exemple.fr": False, "nouveau@exemple.fr": True}


def test_a_successful_enrichment_triggers_a_rescore(db_session: Session) -> None:
    """Section 7.1 : le score est recalculé à chaque fin d'enrichissement."""
    company = _company(db_session, siren="920000027")
    provider = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.HAS_WEBSITE, value_json=False)],
    )

    _orchestrator([provider]).enrich_company(db_session, company)

    snapshots = scoring_repo.latest_scores(db_session, [company.id])
    assert len(snapshots) == 1
    # `has_website = false` laisse `website_url` absent : la règle se déclenche.
    # Les autres règles dépendent de la date du jour, on ne les fige pas ici.
    assert snapshots[0].breakdown["no_website"] == 30
    assert snapshots[0].score >= 30


def test_a_fully_failed_enrichment_does_not_rescore(db_session: Session) -> None:
    """Rien n'a été appris : il n'y a aucune raison de réécrire un score."""
    company = _company(db_session, siren="920000028")
    provider = StubProvider("societeinfo", 0.90, raises=RuntimeError("API indisponible"))

    _orchestrator([provider]).enrich_company(db_session, company)

    assert scoring_repo.latest_scores(db_session, [company.id]) == []


# --- Sélection du lot ---------------------------------------------------


def test_partial_diffusion_companies_are_excluded_from_the_batch(
    db_session: Session,
) -> None:
    """R123-232-1 : les unités non diffusibles ne sont pas prospectables."""
    prospectable = _company(db_session, siren="920000030")
    _company(db_session, siren="920000031", statut_diffusion="P")
    _company(db_session, siren="920000032", etat_administratif="F")

    selected = _orchestrator([]).select_batch(db_session, size=10)

    assert [company.siren for company in selected] == [prospectable.siren]


def test_batch_prioritises_companies_without_any_fact(db_session: Session) -> None:
    """Priorité 1 de la section 6.6, avant les faits expirés."""
    enriched = _company(db_session, siren="920000040")
    never_enriched = _company(db_session, siren="920000041")
    db_session.add(
        EnrichmentFact(
            company_id=enriched.id,
            field=EnrichmentField.HAS_WEBSITE,
            source="societeinfo",
            confidence=0.9,
            collected_at=NOW - timedelta(days=200),
            expires_at=NOW - timedelta(days=110),
        )
    )
    db_session.commit()

    selected = _orchestrator([]).select_batch(db_session, size=10)

    assert [company.siren for company in selected] == [
        never_enriched.siren,
        enriched.siren,
    ]


def test_batch_runs_each_company_in_its_own_session(migrated_engine: Engine) -> None:
    """Parallélisation : une session SQLAlchemy par thread (section 6.6).

    Ce test ne peut pas s'appuyer sur la transaction de test : les threads
    ouvrent leurs propres connexions et ne verraient pas des lignes non
    validées. Les données sont donc réellement écrites, puis nettoyées.
    """
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    provider = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.HAS_WEBSITE, value_json=True)],
    )
    orchestrator = _orchestrator([provider], session_factory=factory)
    setup = factory()

    try:
        for index in range(4):
            _company(setup, siren=f"92000005{index}")

        summary = orchestrator.enrich_batch(setup, size=4)

        assert summary.selected == 4
        assert summary.succeeded == 4
        assert summary.failed == 0
        assert provider.calls == 4
        written = setup.execute(
            select(func.count())
            .select_from(EnrichmentFact)
            .join(Company, Company.id == EnrichmentFact.company_id)
            .where(Company.siren.like("92000005%"))
        ).scalar_one()
        assert written == 4
    finally:
        setup.close()
        with migrated_engine.begin() as connection:
            # Les faits et les runs partent en cascade avec les entreprises.
            connection.execute(text("DELETE FROM companies WHERE siren LIKE '92000005%'"))


def test_default_providers_follow_the_section_6_1_order() -> None:
    """Ordre et confiances du tableau 6.1, à l'identique."""
    providers = default_provider_factory(_settings())()

    assert [(provider.name, provider.confidence) for provider in providers] == [
        ("societeinfo", 0.90),
        ("website_probe", 0.95),
        ("contact_extract", 0.60),
    ]


def test_stats_helpers_report_valid_and_expired_facts(db_session: Session) -> None:
    """Ce que lit `crm enrich stats`."""
    company = _company(db_session, siren="920000070")
    db_session.add_all(
        [
            EnrichmentFact(
                company_id=company.id,
                field=EnrichmentField.HAS_WEBSITE,
                source="societeinfo",
                confidence=0.9,
                collected_at=NOW,
                expires_at=NOW + timedelta(days=90),
            ),
            EnrichmentFact(
                company_id=company.id,
                field=EnrichmentField.WEBSITE_CMS,
                source="website_probe",
                confidence=0.95,
                collected_at=NOW - timedelta(days=200),
                expires_at=NOW - timedelta(days=110),
            ),
        ]
    )
    db_session.commit()

    assert enrichment_repo.count_facts_by_field(db_session) == [("has_website", 1)]
    assert enrichment_repo.count_facts_by_source(db_session) == [("societeinfo", 1)]
    assert enrichment_repo.count_expired_facts(db_session) == 1

    provider = StubProvider(
        "societeinfo",
        0.90,
        facts=[FactCandidate(field=EnrichmentField.HAS_WEBSITE, value_json=True)],
    )
    _orchestrator([provider]).enrich_company(db_session, company)
    runs = enrichment_repo.list_runs(db_session, limit=10)

    assert [run.provider for run in runs] == ["societeinfo"]
    assert runs[0].status == "success"


def test_batch_does_nothing_when_enrichment_is_disabled(db_session: Session) -> None:
    _company(db_session, siren="920000060")
    provider = StubProvider("societeinfo", 0.90)

    summary = _orchestrator([provider], settings=_settings(ENRICHMENT_ENABLED=False)).enrich_batch(
        db_session
    )

    assert summary.selected == 0
    assert provider.calls == 0
