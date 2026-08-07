"""Client SIRENE : quota, pagination par curseur, codes de retour (section 5.1).

Aucun appel réseau réel : `respx` intercepte tout `httpx` (section 13.1).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from app.config import Settings
from app.services.sirene.client import (
    API_KEY_HEADER,
    CHAMPS_PARAM,
    MAX_PAGE_SIZE,
    AuthError,
    QueryError,
    QueryTooLongError,
    QuotaError,
    RateLimiter,
    ServerError,
    SireneClient,
    SireneError,
)

BASE_URL = "https://api.test.insee.fr/api-sirene/3.11"
API_KEY = "cle-de-test"
FixtureLoader = Callable[[str], dict[str, Any]]


class FakeClock:
    """Horloge et sommeil simulés : le temps n'avance que si l'on dort."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "SIRENE_BASE_URL": BASE_URL,
        "SIRENE_API_KEY": API_KEY,
        "SIRENE_RATE_LIMIT_PER_MINUTE": 28,
        "SIRENE_PAGE_SIZE": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_client(clock: FakeClock | None = None, **overrides: Any) -> SireneClient:
    """Client dont le quota et les attentes de retry n'immobilisent rien."""
    clock = clock or FakeClock()
    settings = make_settings(**overrides)
    limiter = RateLimiter(
        settings.SIRENE_RATE_LIMIT_PER_MINUTE,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return SireneClient(settings, limiter=limiter, sleep=clock.sleep)


# --- Seau à jetons ------------------------------------------------------


def test_rate_limiter_blocks_beyond_the_quota() -> None:
    """Quota de 2/min : le troisième jeton coûte 30 secondes d'attente."""
    clock = FakeClock()
    limiter = RateLimiter(2, monotonic=clock.monotonic, sleep=clock.sleep)

    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == []
    assert clock.now == 0.0

    limiter.acquire()

    assert clock.sleeps == [pytest.approx(30.0)]
    assert clock.now == pytest.approx(30.0)


def test_rate_limiter_refills_over_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(60, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(60):
        limiter.acquire()
    assert limiter.available_tokens == pytest.approx(0.0)

    clock.now += 10.0

    assert limiter.available_tokens == pytest.approx(10.0)


def test_rate_limiter_rejects_a_non_positive_quota() -> None:
    with pytest.raises(ValueError, match="strictement positif"):
        RateLimiter(0)


# --- Pagination ---------------------------------------------------------


def test_iter_siret_walks_every_page_and_stops_on_the_last(
    load_fixture: FixtureLoader,
) -> None:
    """Trois pages, arrêt quand `curseurSuivant` égale le curseur envoyé."""
    pages = [load_fixture(f"sirene/{name}.json") for name in ("page_1", "page_2", "page_last")]

    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            side_effect=[httpx.Response(200, json=page) for page in pages]
        )
        with make_client() as client:
            etablissements = list(client.iter_siret("periode(etatAdministratifEtablissement:A)"))

    assert route.call_count == 3
    assert [item["siren"] for item in etablissements] == [
        "000325175",
        "001807254",
        "005410220",
        "912345680",
        "912345681",
        "912345682",
        "912345683",
    ]

    sent_cursors = [call.request.url.params["curseur"] for call in route.calls]
    assert sent_cursors == [
        "*",
        pages[0]["header"]["curseurSuivant"],
        pages[1]["header"]["curseurSuivant"],
    ]


def test_search_never_sends_debut_or_tri(load_fixture: FixtureLoader) -> None:
    """La pagination est par curseur ; `tri` est incompatible avec `curseur`."""
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/page_last.json"))
        )
        with make_client() as client:
            client.search_siret("q")

    params = route.calls[0].request.url.params
    assert "debut" not in params
    assert "tri" not in params
    assert params["curseur"] == "*"
    assert params["champs"] == CHAMPS_PARAM
    assert params["nombre"] == "1000"


def test_request_carries_the_insee_authentication_header(
    load_fixture: FixtureLoader,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/page_last.json"))
        )
        with make_client() as client:
            client.search_siret("q")

    assert route.calls[0].request.headers[API_KEY_HEADER] == API_KEY


def test_page_size_is_capped_at_the_api_maximum(load_fixture: FixtureLoader) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/page_last.json"))
        )
        with make_client(SIRENE_PAGE_SIZE=5000) as client:
            client.search_siret("q")

    assert route.calls[0].request.url.params["nombre"] == str(MAX_PAGE_SIZE)


def test_last_page_is_detected(load_fixture: FixtureLoader) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/siret").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/page_last.json"))
        )
        with make_client() as client:
            page = client.search_siret("q", cursor="AoEpOTg3NjU0MzIxMDAwMjM=")

    assert page.is_last is True
    assert page.total == 6
    assert len(page.etablissements) == 2


# --- Codes de retour ----------------------------------------------------


def test_404_is_an_empty_page_not_an_error(load_fixture: FixtureLoader) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(404, json=load_fixture("sirene/empty_404.json"))
        )
        with make_client() as client:
            page = client.search_siret("q")
            assert list(client.iter_siret("q")) == []

    assert page.total == 0
    assert page.etablissements == []
    assert page.is_last is True
    assert route.call_count == 2


def test_429_is_retried_after_a_wait_and_then_succeeds(
    load_fixture: FixtureLoader,
) -> None:
    clock = FakeClock()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            side_effect=[
                httpx.Response(429, json=load_fixture("sirene/rate_limited_429.json")),
                httpx.Response(200, json=load_fixture("sirene/page_last.json")),
            ]
        )
        with make_client(clock) as client:
            page = client.search_siret("q")

    assert route.call_count == 2
    assert client.request_count == 2
    assert page.total == 6
    # Une attente a bien eu lieu entre les deux tentatives, en plus de celles
    # éventuellement dues au seau à jetons.
    assert any(delay > 0 for delay in clock.sleeps)


def test_429_gives_up_after_five_attempts(load_fixture: FixtureLoader) -> None:
    clock = FakeClock()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(429, json=load_fixture("sirene/rate_limited_429.json"))
        )
        with make_client(clock) as client, pytest.raises(QuotaError):
            client.search_siret("q")

    assert route.call_count == 5


def test_500_is_retried(load_fixture: FixtureLoader) -> None:
    clock = FakeClock()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            side_effect=[
                httpx.Response(500, text="Internal Server Error"),
                httpx.Response(200, json=load_fixture("sirene/page_last.json")),
            ]
        )
        with make_client(clock) as client:
            page = client.search_siret("q")

    assert route.call_count == 2
    assert page.total == 6


def test_503_gives_up_after_five_attempts() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(return_value=httpx.Response(503, text="unavailable"))
        with make_client() as client, pytest.raises(ServerError):
            client.search_siret("q")

    assert route.call_count == 5


def test_400_is_raised_immediately_without_retry() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(400, text="Le format de la requête est incorrect")
        )
        with make_client() as client, pytest.raises(QueryError):
            client.search_siret("q(")

    assert route.call_count == 1


def test_401_is_raised_immediately_without_retry() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(return_value=httpx.Response(401, text="Unauthorized"))
        with make_client() as client, pytest.raises(AuthError):
            client.search_siret("q")

    assert route.call_count == 1


def test_414_is_raised_immediately_without_retry() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(return_value=httpx.Response(414, text="URI Too Long"))
        with make_client() as client, pytest.raises(QueryTooLongError):
            client.search_siret("q" * 5000)

    assert route.call_count == 1


def test_301_is_logged_and_the_payload_is_returned(load_fixture: FixtureLoader) -> None:
    """Succession d'unité : ce n'est pas une erreur, la réponse est rendue."""
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(
            return_value=httpx.Response(
                301,
                json=load_fixture("sirene/page_last.json"),
                headers={"location": f"{BASE_URL}/siret/91234568200011"},
            )
        )
        with make_client() as client:
            page = client.search_siret("q")

    assert route.call_count == 1
    assert page.total == 6


def test_unreadable_body_raises_a_sirene_error() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/siret").mock(return_value=httpx.Response(200, text="pas du json"))
        with make_client() as client, pytest.raises(SireneError, match="illisible"):
            client.search_siret("q")


def test_a_json_array_body_raises_a_sirene_error() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/siret").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
        with make_client() as client, pytest.raises(SireneError, match="objet JSON"):
            client.search_siret("q")


def test_an_unexpected_status_is_not_retried() -> None:
    """403 n'est pas dans la liste de la section 5.1 : erreur, sans retry."""
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/siret").mock(return_value=httpx.Response(403, text="Forbidden"))
        with make_client() as client, pytest.raises(SireneError, match="inattendue"):
            client.search_siret("q")

    assert route.call_count == 1


# --- Service informations ----------------------------------------------


def test_get_informations_reads_the_etablissements_collection_only(
    load_fixture: FixtureLoader,
) -> None:
    """Les autres collections sont ignorées, y compris plus récentes.

    Le collecteur n'interroge que `/siret` ; se caler sur « Liens de
    succession », plus récente d'une seconde, ferait avancer le watermark sans
    qu'aucune donnée établissement n'ait bougé.
    """
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get("/informations").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/informations.json"))
        )
        with make_client() as client:
            infos = client.get_informations()

    assert route.call_count == 1
    # 07:20:43 heure de Paris en août (UTC+2) -> 05:20:43 UTC.
    assert infos.date_derniere_mise_a_disposition == datetime(2026, 8, 5, 5, 20, 43, tzinfo=UTC)
    assert infos.version == "3.11.95"
    assert infos.etat_service == "UP"


def test_get_informations_exposes_the_upper_bound_of_the_window(
    load_fixture: FixtureLoader,
) -> None:
    """`dateDernierTraitementMaximum` borne la fenêtre mieux que `now()`."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/informations.json"))
        )
        with make_client() as client:
            infos = client.get_informations()

    # 00:05:52.648 heure de Paris le 5 août -> 22:05:52.648 UTC la veille.
    assert infos.date_dernier_traitement_maximum == datetime(
        2026, 8, 4, 22, 5, 52, 648000, tzinfo=UTC
    )


def test_get_informations_exposes_the_collection_state(
    load_fixture: FixtureLoader,
) -> None:
    """La clé est « Collection » en majuscule dans `etatsDesServices`."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json=load_fixture("sirene/informations.json"))
        )
        with make_client() as client:
            infos = client.get_informations()

    assert infos.etat_collection == "UP"


def test_get_informations_reports_a_collection_that_is_down(
    load_fixture: FixtureLoader,
) -> None:
    """Hors « UP », le collecteur devra s'abstenir : l'état remonte tel quel."""
    payload = load_fixture("sirene/informations.json")
    payload["etatsDesServices"] = [
        {"Collection": "Unités Légales", "etatCollection": "UP"},
        {"Collection": "Établissements", "etatCollection": "DOWN"},
    ]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(200, json=payload))
        with make_client() as client:
            infos = client.get_informations()

    assert infos.etat_collection == "DOWN"


def test_get_informations_tolerates_a_missing_service_state(
    load_fixture: FixtureLoader,
) -> None:
    payload = load_fixture("sirene/informations.json")
    del payload["etatsDesServices"]
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(200, json=payload))
        with make_client() as client:
            infos = client.get_informations()

    assert infos.etat_collection is None
    assert infos.date_dernier_traitement_maximum is not None


def test_get_informations_raises_when_the_collection_is_missing() -> None:
    """Pas de repli sur une autre collection : l'absence est une erreur."""
    payload = {
        "versionService": "3.11.95",
        "datesDernieresMisesAJourDesDonnees": [
            {
                "collection": "Unités Légales",
                "dateDerniereMiseADisposition": "2026-08-05T07:20:41.000",
            },
            {"collection": 42, "dateDerniereMiseADisposition": "2026-08-05T07:20:42.000"},
            "pas-un-objet",
        ],
    }
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(200, json=payload))
        with make_client() as client, pytest.raises(SireneError, match="etablissements"):
            client.get_informations()


def test_get_informations_raises_on_an_empty_payload() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(
            return_value=httpx.Response(200, json={"header": {"statut": 200}})
        )
        with make_client() as client, pytest.raises(SireneError, match="watermark"):
            client.get_informations()


def test_get_informations_raises_on_an_unreadable_date() -> None:
    payload = {
        "datesDernieresMisesAJourDesDonnees": [
            {"collection": "Établissements", "dateDerniereMiseADisposition": "05/08/2026"}
        ]
    }
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(200, json=payload))
        with make_client() as client, pytest.raises(SireneError, match="illisible"):
            client.get_informations()


def test_get_informations_tolerates_a_missing_maximum_processing_date() -> None:
    payload = {
        "datesDernieresMisesAJourDesDonnees": [
            {
                "collection": "Établissements",
                "dateDerniereMiseADisposition": "2026-08-05T07:20:43.000",
            }
        ]
    }
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/informations").mock(return_value=httpx.Response(200, json=payload))
        with make_client() as client:
            infos = client.get_informations()

    assert infos.date_dernier_traitement_maximum is None
