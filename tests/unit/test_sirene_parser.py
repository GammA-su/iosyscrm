"""Normalisation d'un établissement SIRENE (section 5.3)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.services.sirene.parser import (
    NOMENCLATURE_RAW_KEY,
    CompanyPayload,
    departement_from_code_commune,
    parse_api_datetime,
    parse_etablissement,
    sanitize_raw,
)

FixtureLoader = Callable[[str], dict[str, Any]]


def first_etablissement(payload: dict[str, Any]) -> dict[str, Any]:
    etablissement = payload["etablissements"][0]
    assert isinstance(etablissement, dict)
    return etablissement


def test_personne_morale_uses_the_denomination(load_fixture: FixtureLoader) -> None:
    raw = first_etablissement(load_fixture("sirene/page_1.json"))

    payload = parse_etablissement(raw)

    assert payload.siren == "912345678"
    assert payload.siret_siege == "91234567800015"
    assert payload.denomination == "ALSACE NUMERIQUE"
    assert payload.nom_complet is None
    assert payload.is_personne_physique is False
    assert payload.categorie_juridique == "5710"
    assert payload.activite_principale == "62.01Y"
    assert payload.tranche_effectifs == "02"
    assert payload.etat_administratif == "A"
    assert payload.statut_diffusion == "O"
    assert payload.date_creation == date(2026, 6, 12)
    # 09:41:22 heure de Paris en juin (UTC+2) -> 07:41:22 UTC.
    assert payload.date_dernier_traitement == datetime(2026, 6, 18, 7, 41, 22, tzinfo=UTC)


def test_personne_physique_builds_the_full_name(load_fixture: FixtureLoader) -> None:
    """Nom puis prénom usuel, et le drapeau `is_personne_physique` est posé."""
    raw = first_etablissement(load_fixture("sirene/personne_physique.json"))

    payload = parse_etablissement(raw)

    assert payload.is_personne_physique is True
    assert payload.nom_complet == "SCHMITT Marie"
    assert payload.denomination is None
    assert payload.commune == "GUEBWILLER"
    assert payload.departement == "68"


def test_diffusion_partielle_is_parsed_without_raising(
    load_fixture: FixtureLoader,
) -> None:
    """Les champs masqués valent `None`, l'unité reste exploitable."""
    raw = first_etablissement(load_fixture("sirene/diffusion_partielle.json"))

    payload = parse_etablissement(raw)

    assert payload.statut_diffusion == "P"
    assert payload.is_diffusion_partielle is True
    assert payload.siren == "913000111"
    assert payload.denomination is None
    assert payload.nom_complet is None
    assert payload.adresse_libelle_voie is None
    assert payload.code_postal is None
    assert payload.activite_principale is None
    # Ce qui n'est pas masqué reste renseigné.
    assert payload.departement == "68"
    assert payload.date_creation == date(2026, 5, 4)
    assert payload.etat_administratif == "A"


def test_partial_diffusion_personne_physique_is_detected_by_legal_category(
    load_fixture: FixtureLoader,
) -> None:
    """Catégorie 1000 : entrepreneur individuel, même sans nom patronymique.

    C'est le seul signal disponible en diffusion partielle, et ce sont
    précisément ces unités à ne pas prospecter (section 8).
    """
    raw = first_etablissement(load_fixture("sirene/diffusion_partielle.json"))

    payload = parse_etablissement(raw)

    assert payload.categorie_juridique == "1000"
    assert payload.is_personne_physique is True
    assert payload.nom_complet is None


def test_legal_category_wins_over_the_absence_of_a_patronymic_name() -> None:
    """Une catégorie juridique renseignée prime : 5710 reste une personne morale."""
    payload = parse_etablissement(
        {
            "siren": "917000111",
            "siret": "91700011100011",
            "statutDiffusionEtablissement": "P",
            "uniteLegale": {
                "denominationUniteLegale": None,
                "nomUniteLegale": None,
                "prenomUsuelUniteLegale": None,
                "categorieJuridiqueUniteLegale": "5710",
            },
        }
    )

    assert payload.is_personne_physique is False
    assert payload.nom_complet is None


def test_patronymic_name_is_the_fallback_signal() -> None:
    """Sans catégorie juridique, le nom patronymique fait foi."""
    payload = parse_etablissement(
        {
            "siren": "917000112",
            "siret": "91700011200019",
            "statutDiffusionEtablissement": "O",
            "uniteLegale": {
                "denominationUniteLegale": None,
                "nomUniteLegale": "WEBER",
                "prenomUsuelUniteLegale": "Luc",
                "categorieJuridiqueUniteLegale": None,
            },
        }
    )

    assert payload.is_personne_physique is True
    assert payload.nom_complet == "WEBER Luc"


# --- Fuseau horaire -----------------------------------------------------


def test_summer_timestamp_is_converted_from_paris_to_utc(
    load_fixture: FixtureLoader,
) -> None:
    """Juillet : Paris est à UTC+2, 14:30 locales valent 12:30 UTC."""
    raw = load_fixture("sirene/horodatages_ete_hiver.json")["etablissements"][0]

    payload = parse_etablissement(raw)

    assert payload.date_dernier_traitement == datetime(2026, 7, 15, 12, 30, tzinfo=UTC)
    assert payload.date_dernier_traitement is not None
    assert payload.date_dernier_traitement.tzinfo is not None


def test_winter_timestamp_is_converted_from_paris_to_utc(
    load_fixture: FixtureLoader,
) -> None:
    """Janvier : Paris est à UTC+1, 14:30 locales valent 13:30 UTC."""
    raw = load_fixture("sirene/horodatages_ete_hiver.json")["etablissements"][1]

    payload = parse_etablissement(raw)

    assert payload.date_dernier_traitement == datetime(2026, 1, 15, 13, 30, tzinfo=UTC)


def test_an_already_aware_timestamp_is_only_shifted_to_utc() -> None:
    assert parse_api_datetime("2026-07-15T14:30:00+00:00") == datetime(
        2026, 7, 15, 14, 30, tzinfo=UTC
    )


@pytest.mark.parametrize("value", [None, "", "31/12/2026", 20260715])
def test_unusable_timestamps_return_none(value: Any) -> None:
    assert parse_api_datetime(value) is None


def test_address_is_flattened(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("sirene/page_1.json")["etablissements"][1]

    payload = parse_etablissement(raw)

    assert payload.adresse_numero == "4"
    assert payload.adresse_type_voie == "AVENUE"
    assert payload.adresse_libelle_voie == "DE COLMAR"
    assert payload.adresse_complement == "BATIMENT B"
    assert payload.code_postal == "68100"
    assert payload.code_commune == "68224"


def test_raw_payload_is_preserved(load_fixture: FixtureLoader) -> None:
    """`companies.raw` conserve la réponse brute, expurgée (sections 3.2 et 8)."""
    raw = first_etablissement(load_fixture("sirene/page_1.json"))

    payload = parse_etablissement(raw)

    assert payload.raw["siren"] == raw["siren"]
    assert payload.raw["adresseEtablissement"] == raw["adresseEtablissement"]
    assert payload.raw["periodesEtablissement"] == raw["periodesEtablissement"]
    assert payload.raw["nic"] == "00015"
    assert payload.raw["nombrePeriodesEtablissement"] == 1


# --- Nomenclature d'activité -------------------------------------------


def test_naf25_is_preferred_over_the_period_code(load_fixture: FixtureLoader) -> None:
    """NAF 2025 est la nomenclature en vigueur ; NAFRev2 n'est qu'un repli."""
    raw = first_etablissement(load_fixture("sirene/page_1.json"))
    assert raw["periodesEtablissement"][0]["activitePrincipaleEtablissement"] == "62.01Z"

    payload = parse_etablissement(raw)

    assert payload.activite_principale == "62.01Y"
    assert payload.raw[NOMENCLATURE_RAW_KEY] == "NAF2025"


def test_naf25_and_the_period_code_share_the_two_digit_prefix(
    load_fixture: FixtureLoader,
) -> None:
    """Le filtrage NAF et `target_sector` sont donc insensibles au changement."""
    raw = first_etablissement(load_fixture("sirene/page_1.json"))
    period_code = raw["periodesEtablissement"][0]["activitePrincipaleEtablissement"]

    payload = parse_etablissement(raw)

    assert payload.activite_principale is not None
    assert payload.activite_principale[:2] == period_code[:2]


def test_period_code_is_used_when_naf25_is_absent() -> None:
    payload = parse_etablissement(
        {
            "siren": "918000111",
            "siret": "91800011100017",
            "statutDiffusionEtablissement": "O",
            "activitePrincipaleNAF25Etablissement": None,
            "periodesEtablissement": [
                {
                    "dateFin": None,
                    "activitePrincipaleEtablissement": "32.12Z",
                    "nomenclatureActivitePrincipaleEtablissement": "NAFRev2",
                }
            ],
        }
    )

    assert payload.activite_principale == "32.12Z"
    assert payload.raw[NOMENCLATURE_RAW_KEY] == "NAFRev2"


def test_missing_activity_leaves_the_nomenclature_empty(
    load_fixture: FixtureLoader,
) -> None:
    raw = first_etablissement(load_fixture("sirene/diffusion_partielle.json"))

    payload = parse_etablissement(raw)

    assert payload.activite_principale is None
    assert payload.raw[NOMENCLATURE_RAW_KEY] is None


# --- Tranche d'effectifs ------------------------------------------------


def test_establishment_headcount_range_wins(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("sirene/page_1.json")["etablissements"][1]
    assert raw["trancheEffectifsEtablissement"] == "01"
    assert raw["uniteLegale"]["trancheEffectifsUniteLegale"] == "03"

    assert parse_etablissement(raw).tranche_effectifs == "01"


def test_nn_headcount_range_falls_back_to_the_legal_unit(
    load_fixture: FixtureLoader,
) -> None:
    """« NN » signifie « non renseigné » : c'est une absence, pas une valeur."""
    raw = first_etablissement(load_fixture("sirene/page_1.json"))
    assert raw["trancheEffectifsEtablissement"] == "NN"

    assert parse_etablissement(raw).tranche_effectifs == "02"


def test_nn_everywhere_stores_none() -> None:
    payload = parse_etablissement(
        {
            "siren": "918000112",
            "siret": "91800011200015",
            "statutDiffusionEtablissement": "O",
            "trancheEffectifsEtablissement": "NN",
            "uniteLegale": {"trancheEffectifsUniteLegale": "NN"},
        }
    )

    assert payload.tranche_effectifs is None


# --- Minimisation des données personnelles ------------------------------


def test_sanitize_raw_drops_personal_fields(load_fixture: FixtureLoader) -> None:
    """Sexe, prénoms numérotés et pseudonyme n'ont aucune finalité commerciale."""
    raw = first_etablissement(load_fixture("sirene/personne_physique.json"))
    assert raw["uniteLegale"]["sexeUniteLegale"] == "F"
    assert raw["uniteLegale"]["prenom1UniteLegale"] == "Marie"

    sanitized = sanitize_raw(raw)

    unite = sanitized["uniteLegale"]
    assert "sexeUniteLegale" not in unite
    assert "prenom1UniteLegale" not in unite
    assert "prenom2UniteLegale" not in unite
    assert "prenom3UniteLegale" not in unite
    assert "prenom4UniteLegale" not in unite
    assert "pseudonymeUniteLegale" not in unite
    # Le prénom usuel reste : il compose `nom_complet`.
    assert unite["prenomUsuelUniteLegale"] == "Marie"
    assert unite["nomUniteLegale"] == "SCHMITT"


def test_sanitize_raw_does_not_mutate_the_api_response(
    load_fixture: FixtureLoader,
) -> None:
    raw = first_etablissement(load_fixture("sirene/personne_physique.json"))

    sanitize_raw(raw)

    assert raw["uniteLegale"]["sexeUniteLegale"] == "F"


def test_stored_raw_is_sanitized(load_fixture: FixtureLoader) -> None:
    raw = first_etablissement(load_fixture("sirene/personne_physique.json"))

    payload = parse_etablissement(raw)

    assert "sexeUniteLegale" not in payload.raw["uniteLegale"]
    assert payload.raw["uniteLegale"]["prenomUsuelUniteLegale"] == "Marie"
    assert payload.nom_complet == "SCHMITT Marie"


def test_sanitize_raw_tolerates_a_missing_legal_unit() -> None:
    assert sanitize_raw({"siren": "918000113"}) == {"siren": "918000113"}


@pytest.mark.parametrize(
    ("code_commune", "expected"),
    [
        ("68066", "68"),
        ("68224", "68"),
        ("67482", "67"),
        ("90010", "90"),
        ("97411", "974"),
        ("97209", "972"),
        ("  68066 ", "68"),
        (None, None),
        ("", None),
    ],
)
def test_departement_is_two_characters_and_three_for_the_dom(
    code_commune: str | None, expected: str | None
) -> None:
    assert departement_from_code_commune(code_commune) == expected


def test_dom_establishment_gets_a_three_character_departement(
    load_fixture: FixtureLoader,
) -> None:
    raw = load_fixture("sirene/page_last.json")["etablissements"][1]

    payload = parse_etablissement(raw)

    assert payload.code_commune == "97411"
    assert payload.departement == "974"


def test_current_period_is_the_one_without_an_end_date() -> None:
    raw: dict[str, Any] = {
        "siren": "915000111",
        "siret": "91500011100019",
        "statutDiffusionEtablissement": "O",
        "periodesEtablissement": [
            {
                "dateFin": None,
                "dateDebut": "2026-03-01",
                "etatAdministratifEtablissement": "F",
                "activitePrincipaleEtablissement": "70.22Z",
            },
            {
                "dateFin": "2026-02-28",
                "dateDebut": "2025-01-01",
                "etatAdministratifEtablissement": "A",
                "activitePrincipaleEtablissement": "62.01Z",
            },
        ],
    }

    payload = parse_etablissement(raw)

    assert payload.etat_administratif == "F"
    assert payload.activite_principale == "70.22Z"


def test_first_period_is_used_when_none_is_open() -> None:
    """Toutes les périodes closes : la plus récente, en tête, fait foi."""
    raw: dict[str, Any] = {
        "siren": "915000114",
        "siret": "91500011400013",
        "statutDiffusionEtablissement": "O",
        "periodesEtablissement": [
            {
                "dateFin": "2026-05-31",
                "dateDebut": "2026-03-01",
                "etatAdministratifEtablissement": "F",
                "activitePrincipaleEtablissement": "43.32A",
            },
            {
                "dateFin": "2026-02-28",
                "dateDebut": "2025-01-01",
                "etatAdministratifEtablissement": "A",
                "activitePrincipaleEtablissement": "62.01Z",
            },
        ],
    }

    payload = parse_etablissement(raw)

    assert payload.etat_administratif == "F"
    assert payload.activite_principale == "43.32A"


def test_missing_periods_do_not_raise() -> None:
    payload = parse_etablissement(
        {"siren": "915000112", "siret": "91500011200017", "statutDiffusionEtablissement": "O"}
    )

    assert payload.etat_administratif is None
    assert payload.activite_principale is None
    assert payload.departement is None


def test_malformed_dates_are_ignored() -> None:
    payload = parse_etablissement(
        {
            "siren": "915000113",
            "siret": "91500011300015",
            "statutDiffusionEtablissement": "O",
            "dateCreationEtablissement": "pas-une-date",
            "dateDernierTraitementEtablissement": "31/12/2026",
        }
    )

    assert payload.date_creation is None
    assert payload.date_dernier_traitement is None


def test_empty_payload_defaults_to_open_diffusion() -> None:
    payload = parse_etablissement({})

    assert isinstance(payload, CompanyPayload)
    assert payload.siren == ""
    assert payload.statut_diffusion == "O"
    assert payload.is_diffusion_partielle is False
