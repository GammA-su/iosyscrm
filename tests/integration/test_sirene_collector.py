"""Collecteur SIRENE de bout en bout : PostgreSQL réel et HTTP intercepté."""

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.collector import CollectorRun
from app.models.company import Company
from app.repositories import collector as collector_repo
from app.repositories import company as company_repo
from app.repositories.company import UpsertResult
from app.services.sirene.client import AuthError, SireneClient
from app.services.sirene.collector import (
    LAST_DISPOSITION_WATERMARK,
    LAST_TRAITEMENT_WATERMARK,
    SireneCollector,
)
from app.services.sirene.parser import CompanyPayload

BASE_URL = "https://api.test.insee.fr/api-sirene/3.11"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
FixtureLoader = Callable[[str], dict[str, Any]]


def _settings(
    *,
    departments: list[str] | None = None,
    naf_exclude: list[str] | None = None,
) -> Settings:
    return Settings(
        _env_file=None,
        SIRENE_BASE_URL=BASE_URL,
        SIRENE_API_KEY="cle-test",
        SIRENE_RATE_LIMIT_PER_MINUTE=1000,
        SIRENE_PAGE_SIZE=1000,
        SIRENE_LOOKBACK_DAYS=90,
        SIRENE_DEPARTEMENTS=departments or [],
        SIRENE_NAF_EXCLUDE=naf_exclude or [],
    )


def _page(
    etablissements: list[dict[str, Any]],
    *,
    cursor: str,
    next_cursor: str,
    total: int | None = None,
) -> dict[str, Any]:
    return {
        "header": {
            "statut": 200,
            "message": "OK",
            "total": total if total is not None else len(etablissements),
            "nombre": len(etablissements),
            "curseur": cursor,
            "curseurSuivant": next_cursor,
        },
        "etablissements": etablissements,
    }


def _three_pages(load_fixture: FixtureLoader) -> list[dict[str, Any]]:
    """Trois pages cohérentes construites à partir des fixtures ciblées T3."""
    first = copy.deepcopy(load_fixture("sirene/page_2.json")["etablissements"])
    second = copy.deepcopy(load_fixture("sirene/page_last.json")["etablissements"])
    partial = copy.deepcopy(load_fixture("sirene/diffusion_partielle.json")["etablissements"])
    return [
        _page(first, cursor="*", next_cursor="cursor-2", total=5),
        _page(second, cursor="cursor-2", next_cursor="cursor-3", total=5),
        _page(partial, cursor="cursor-3", next_cursor="cursor-3", total=5),
    ]


def _informations(
    load_fixture: FixtureLoader,
    *,
    disposition: str | None = None,
    maximum: str | None = "2026-08-05T00:05:52.648",
    state: str = "UP",
) -> dict[str, Any]:
    payload = copy.deepcopy(load_fixture("sirene/informations.json"))
    for entry in payload["datesDernieresMisesAJourDesDonnees"]:
        if entry["collection"] == "Établissements":
            if disposition is not None:
                entry["dateDerniereMiseADisposition"] = disposition
            entry["dateDernierTraitementMaximum"] = maximum
    for entry in payload["etatsDesServices"]:
        if entry["Collection"] == "Établissements":
            entry["etatCollection"] = state
    return payload


def _company_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Company)).scalar_one()


def test_sync_walks_three_pages_and_persists_the_expected_companies(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings(departments=["68", "97"])
    pages = _three_pages(load_fixture)

    with respx.mock(base_url=BASE_URL) as mock:
        info_route = mock.get("/informations").mock(
            return_value=httpx.Response(200, json=_informations(load_fixture))
        )
        search_route = mock.get("/siret").mock(
            side_effect=[httpx.Response(200, json=page) for page in pages]
        )
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: NOW).sync(db_session)

    assert info_route.call_count == 1
    assert search_route.call_count == 3
    assert run.status == "success"
    assert run.records_seen == 5
    assert run.records_new == 5
    assert run.records_updated == 0
    assert run.api_calls == 3
    assert _company_count(db_session) == 5

    query = search_route.calls[0].request.url.params["q"]
    assert "periode(etatAdministratifEtablissement:A)" in query
    assert "etablissementSiege:true" in query
    assert (
        "dateDernierTraitementEtablissement:"
        "[2026-07-29T14:00:00.000 TO 2026-08-05T00:05:52.648]" in query
    )
    assert "dateCreationEtablissement:[2026-05-07 TO 2026-08-05]" in query
    assert "(codeCommuneEtablissement:68* OR codeCommuneEtablissement:97*)" in query

    assert (
        collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK)
        == "2026-08-04T22:05:52.648000+00:00"
    )
    assert (
        collector_repo.get_watermark(db_session, LAST_DISPOSITION_WATERMARK)
        == "2026-08-05T05:20:43+00:00"
    )

    partial = company_repo.get_by_siren(db_session, "913000111")
    assert partial is not None
    assert partial.statut_diffusion == "P"
    assert partial.denomination is None
    assert partial.nom_complet is None
    assert "prenom1UniteLegale" not in partial.raw["uniteLegale"]

    enrichment_candidates = company_repo.list_for_enrichment(db_session, limit=10)
    assert len(enrichment_candidates) == 4
    assert all(company.statut_diffusion != "P" for company in enrichment_candidates)


def test_two_syncs_update_without_creating_duplicates(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings()
    pages = _three_pages(load_fixture)
    first_info = _informations(load_fixture)
    second_info = _informations(
        load_fixture,
        disposition="2026-08-05T08:20:43.000",
        maximum="2026-08-05T01:05:52.648",
    )

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            side_effect=[
                httpx.Response(200, json=first_info),
                httpx.Response(200, json=second_info),
            ]
        )
        search_route = mock.get("/siret").mock(
            side_effect=[httpx.Response(200, json=page) for page in [*pages, *copy.deepcopy(pages)]]
        )
        with SireneClient(settings) as client:
            collector = SireneCollector(settings, client=client, now=lambda: NOW)
            first_run = collector.sync(db_session)
            second_run = collector.sync(db_session)

    assert search_route.call_count == 6
    assert first_run.records_new == 5
    assert first_run.records_updated == 0
    assert second_run.records_new == 0
    assert second_run.records_updated == 5
    assert _company_count(db_session) == 5


def test_unchanged_disposition_skips_the_search_route(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings()
    collector_repo.set_watermark(
        db_session,
        LAST_DISPOSITION_WATERMARK,
        "2026-08-05T05:20:43+00:00",
    )
    db_session.commit()

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=_informations(load_fixture))
        )
        search_route = mock.get("/siret").mock(
            return_value=httpx.Response(200, json=_three_pages(load_fixture)[0])
        )
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: NOW).sync(db_session)

    assert search_route.call_count == 0
    assert run.status == "success"
    assert run.records_seen == 0
    assert run.api_calls == 0
    assert _company_count(db_session) == 0


def test_unavailable_collection_skips_search_and_watermarks(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings()

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(
                200,
                json=_informations(load_fixture, state="MAINTENANCE"),
            )
        )
        search_route = mock.get("/siret").mock(
            return_value=httpx.Response(200, json=_three_pages(load_fixture)[0])
        )
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: NOW).sync(db_session)

    assert search_route.call_count == 0
    assert run.status == "success"
    assert run.records_seen == 0
    assert collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK) is None
    assert collector_repo.get_watermark(db_session, LAST_DISPOSITION_WATERMARK) is None


def test_information_failure_creates_a_failed_run_without_watermarks(
    db_session: Session,
) -> None:
    settings = _settings()

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(401, text="Unauthorized"))
        with SireneClient(settings) as client:
            collector = SireneCollector(settings, client=client, now=lambda: NOW)
            with pytest.raises(AuthError):
                collector.sync(db_session)

    run = collector_repo.list_runs(db_session, limit=1)[0]
    assert run.status == "failed"
    assert run.records_seen == 0
    assert run.api_calls == 0
    assert run.error is not None
    assert "AuthError" in run.error
    assert collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK) is None
    assert collector_repo.get_watermark(db_session, LAST_DISPOSITION_WATERMARK) is None


def test_failure_on_second_batch_keeps_first_batch_and_old_watermarks(
    db_session: Session,
    load_fixture: FixtureLoader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    template = load_fixture("sirene/page_2.json")["etablissements"][0]
    establishments: list[dict[str, Any]] = []
    for index in range(501):
        raw = copy.deepcopy(template)
        siren = f"{200_000_000 + index:09d}"
        raw["siren"] = siren
        raw["siret"] = f"{siren}00017"
        establishments.append(raw)
    pages = [
        _page(establishments[:500], cursor="*", next_cursor="cursor-2", total=501),
        _page(establishments[500:], cursor="cursor-2", next_cursor="cursor-2", total=501),
    ]

    old_traitement = "2026-07-01T00:00:00+00:00"
    old_disposition = "2026-07-01T06:00:00+00:00"
    collector_repo.set_watermark(db_session, LAST_TRAITEMENT_WATERMARK, old_traitement)
    collector_repo.set_watermark(db_session, LAST_DISPOSITION_WATERMARK, old_disposition)
    db_session.commit()

    original_upsert = company_repo.upsert_many
    upsert_calls = 0

    def fail_on_second_upsert(
        db: Session,
        payloads: list[CompanyPayload],
    ) -> UpsertResult:
        nonlocal upsert_calls
        upsert_calls += 1
        if upsert_calls == 2:
            raise RuntimeError("échec forcé du deuxième lot")
        return original_upsert(db, payloads)

    monkeypatch.setattr(company_repo, "upsert_many", fail_on_second_upsert)

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=_informations(load_fixture))
        )
        search_route = mock.get("/siret").mock(
            side_effect=[httpx.Response(200, json=page) for page in pages]
        )
        with SireneClient(settings) as client:
            collector = SireneCollector(settings, client=client, now=lambda: NOW)
            with pytest.raises(RuntimeError, match="deuxième lot"):
                collector.sync(db_session)

    assert upsert_calls == 2
    assert (
        "dateDernierTraitementEtablissement:"
        "[2026-06-30T02:00:00.000 TO 2026-08-05T00:05:52.648]"
        in search_route.calls[0].request.url.params["q"]
    )
    assert _company_count(db_session) == 500
    assert collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK) == old_traitement
    assert collector_repo.get_watermark(db_session, LAST_DISPOSITION_WATERMARK) == old_disposition

    run = collector_repo.list_runs(db_session, limit=1)[0]
    assert run.status == "failed"
    assert run.records_seen == 501
    assert run.records_new == 500
    assert run.records_updated == 0
    assert run.error is not None
    assert "échec forcé du deuxième lot" in run.error


def test_excluded_naf_prefix_is_not_inserted(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings(naf_exclude=["85"])
    excluded = copy.deepcopy(load_fixture("sirene/page_1.json")["etablissements"][1])
    allowed = copy.deepcopy(load_fixture("sirene/page_2.json")["etablissements"][0])
    page = _page([excluded, allowed], cursor="*", next_cursor="*", total=2)

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=_informations(load_fixture))
        )
        mock.get("/siret").mock(return_value=httpx.Response(200, json=page))
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: NOW).sync(db_session)

    assert run.records_seen == 2
    assert run.records_new == 1
    assert company_repo.get_by_siren(db_session, excluded["siren"]) is None
    assert company_repo.get_by_siren(db_session, allowed["siren"]) is not None


def test_incomplete_payload_is_rejected_without_failing_the_batch(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    """Une ligne sans date de création ne doit pas emporter tout le lot."""
    settings = _settings()
    valid = copy.deepcopy(load_fixture("sirene/page_2.json")["etablissements"])
    incomplete = copy.deepcopy(valid[0])
    incomplete["siren"] = "919111222"
    incomplete["siret"] = "91911122200015"
    incomplete["dateCreationEtablissement"] = None
    page = _page([*valid, incomplete], cursor="*", next_cursor="*", total=3)

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=_informations(load_fixture))
        )
        mock.get("/siret").mock(return_value=httpx.Response(200, json=page))
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: NOW).sync(db_session)

    assert run.status == "success"
    assert run.records_seen == 3
    assert run.records_new == 2
    assert run.records_rejected == 1
    assert _company_count(db_session) == 2
    assert company_repo.get_by_siren(db_session, "919111222") is None


def test_missing_maximum_falls_back_to_now(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    """Sans `dateDernierTraitementMaximum`, la borne haute retombe sur `now()`.

    L'horloge injectée est naïve : un instant sans fuseau doit être interprété
    en UTC, jamais rejeté, sinon la fenêtre serait décalée sans le dire.
    """
    settings = _settings()
    naive_now = NOW.replace(tzinfo=None)

    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(
                200,
                json=_informations(load_fixture, maximum=None),
            )
        )
        search_route = mock.get("/siret").mock(
            return_value=httpx.Response(
                404,
                json=load_fixture("sirene/empty_404.json"),
            )
        )
        with SireneClient(settings) as client:
            run = SireneCollector(settings, client=client, now=lambda: naive_now).sync(db_session)

    assert run.status == "success"
    assert run.window_end == NOW
    assert run.api_calls == 1
    assert "TO 2026-08-05T14:00:00.000]" in search_route.calls[0].request.url.params["q"]
    assert collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK) == NOW.isoformat()


def test_repeated_backfill_is_idempotent_and_never_writes_watermarks(
    db_session: Session,
    load_fixture: FixtureLoader,
) -> None:
    settings = _settings(departments=["68", "67"])
    page = load_fixture("sirene/personne_physique.json")

    with respx.mock(base_url=BASE_URL) as mock:
        search_route = mock.get("/siret").mock(
            side_effect=[
                httpx.Response(200, json=page),
                httpx.Response(200, json=page),
            ]
        )
        with SireneClient(settings) as client:
            collector = SireneCollector(settings, client=client, now=lambda: NOW)
            first_run = collector.backfill(db_session, days=7)
            second_run = collector.backfill(db_session, days=7)

    assert search_route.call_count == 2
    assert first_run.records_new == 1
    assert first_run.records_updated == 0
    assert second_run.records_new == 0
    assert second_run.records_updated == 1
    assert _company_count(db_session) == 1
    assert collector_repo.get_watermark(db_session, LAST_TRAITEMENT_WATERMARK) is None
    assert collector_repo.get_watermark(db_session, LAST_DISPOSITION_WATERMARK) is None

    for call in search_route.calls:
        query = call.request.url.params["q"]
        assert "dateCreationEtablissement:[2026-07-29 TO 2026-08-05]" in query
        assert "(codeCommuneEtablissement:68* OR codeCommuneEtablissement:67*)" in query
        # Le rattrapage ignore le watermark : il filtre sur la date de création
        # et jamais sur la date de dernier traitement.
        assert "dateDernierTraitementEtablissement" not in query


def test_backfill_rejects_a_non_positive_window(db_session: Session) -> None:
    """Horloge par défaut : le collecteur est construit comme par la CLI."""
    settings = _settings()
    with SireneClient(settings) as client:
        collector = SireneCollector(settings, client=client)
        with pytest.raises(ValueError, match="strictement positif"):
            collector.backfill(db_session, days=0)

    assert db_session.execute(select(func.count()).select_from(CollectorRun)).scalar_one() == 0
