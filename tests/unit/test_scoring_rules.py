"""Les neuf prédicats du tableau 7.2, et rien d'autre."""

from datetime import date
from typing import Any

import pytest

from app.models.company import Company
from app.services.scoring.engine import ScoringContext
from app.services.scoring.rules import (
    PREDICATES,
    UnknownPredicateError,
    resolve_predicate,
)

TODAY = date(2026, 8, 6)


def _context(
    *,
    facts: dict[str, Any] | None = None,
    channels: frozenset[str] = frozenset(),
    date_creation: date = date(2020, 1, 1),
    activite: str | None = "62.01Z",
    departement: str | None = "75",
    categorie: str | None = "5710",
) -> ScoringContext:
    company = Company(
        id=1,
        siren="912345680",
        siret_siege="91234568000017",
        date_creation=date_creation,
        activite_principale=activite,
        departement=departement,
        categorie_juridique=categorie,
    )
    return ScoringContext(
        company=company,
        facts=facts or {},
        contact_channels=channels,
        today=TODAY,
    )


def _run(name: str, params: dict[str, Any], context: ScoringContext) -> bool:
    return resolve_predicate(name)(context, params)


def test_registry_contains_exactly_the_nine_predicates() -> None:
    assert set(PREDICATES) == {
        "fact_missing",
        "fact_equals",
        "fact_lt",
        "fact_gt",
        "age_days_lt",
        "naf_prefix_in",
        "departement_in",
        "has_contact",
        "legal_form_in",
    }


def test_unknown_predicate_raises_an_explicit_error() -> None:
    with pytest.raises(UnknownPredicateError, match="fact_between"):
        resolve_predicate("fact_between")


# --- Faits --------------------------------------------------------------


def test_fact_missing() -> None:
    params = {"field": "website_url"}

    assert _run("fact_missing", params, _context()) is True
    assert _run("fact_missing", params, _context(facts={"website_url": "https://x.fr"})) is False


def test_fact_equals() -> None:
    params = {"field": "website_https", "value": False}

    assert _run("fact_equals", params, _context(facts={"website_https": False})) is True
    assert _run("fact_equals", params, _context(facts={"website_https": True})) is False
    assert _run("fact_equals", params, _context()) is False


def test_fact_equals_does_not_confuse_false_with_zero() -> None:
    assert _run("fact_equals", {"field": "f", "value": False}, _context(facts={"f": 0})) is False
    assert _run("fact_equals", {"field": "f", "value": 0}, _context(facts={"f": False})) is False


def test_fact_lt() -> None:
    params = {"field": "website_quality_score", "value": 50}

    assert _run("fact_lt", params, _context(facts={"website_quality_score": 35})) is True
    assert _run("fact_lt", params, _context(facts={"website_quality_score": 50})) is False
    assert _run("fact_lt", params, _context(facts={"website_quality_score": 80})) is False
    assert _run("fact_lt", params, _context()) is False


def test_fact_gt() -> None:
    params = {"field": "website_ttfb_ms", "value": 1500}

    assert _run("fact_gt", params, _context(facts={"website_ttfb_ms": 2400})) is True
    assert _run("fact_gt", params, _context(facts={"website_ttfb_ms": 1500})) is False
    assert _run("fact_gt", params, _context()) is False


def test_numeric_predicates_ignore_non_numeric_facts() -> None:
    assert _run("fact_lt", {"field": "f", "value": 50}, _context(facts={"f": "35"})) is False
    assert _run("fact_gt", {"field": "f", "value": 50}, _context(facts={"f": True})) is False


# --- Entreprise ---------------------------------------------------------


def test_age_days_lt() -> None:
    params = {"days": 30}

    assert _run("age_days_lt", params, _context(date_creation=date(2026, 7, 20))) is True
    # 8 juillet -> 29 jours : dedans. 7 juillet -> 30 jours : dehors, « moins de ».
    assert _run("age_days_lt", params, _context(date_creation=date(2026, 7, 8))) is True
    assert _run("age_days_lt", params, _context(date_creation=date(2026, 7, 7))) is False


def test_naf_prefix_in() -> None:
    params = {"prefixes": ["41", "43", "68"]}

    assert _run("naf_prefix_in", params, _context(activite="43.32A")) is True
    assert _run("naf_prefix_in", params, _context(activite="43.32B")) is True
    assert _run("naf_prefix_in", params, _context(activite="62.01Z")) is False
    assert _run("naf_prefix_in", params, _context(activite=None)) is False


def test_departement_in() -> None:
    params = {"codes": ["68", "67", "90"]}

    assert _run("departement_in", params, _context(departement="68")) is True
    assert _run("departement_in", params, _context(departement="75")) is False
    assert _run("departement_in", params, _context(departement=None)) is False


def test_has_contact() -> None:
    assert (
        _run("has_contact", {"channel": "email"}, _context(channels=frozenset({"email"}))) is True
    )
    assert (
        _run("has_contact", {"channel": "phone"}, _context(channels=frozenset({"email"}))) is False
    )
    assert _run("has_contact", {"channel": "email"}, _context()) is False


def test_legal_form_in() -> None:
    params = {"codes": ["5710", "5499"]}

    assert _run("legal_form_in", params, _context(categorie="5710")) is True
    assert _run("legal_form_in", params, _context(categorie="1000")) is False
    assert _run("legal_form_in", params, _context(categorie=None)) is False


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("fact_missing", {}),
        ("fact_lt", {"field": "f"}),
        ("age_days_lt", {}),
        ("naf_prefix_in", {}),
        ("departement_in", {}),
        ("has_contact", {}),
        ("legal_form_in", {}),
    ],
)
def test_missing_parameters_raise(name: str, params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="manquant"):
        _run(name, params, _context(facts={"f": 10}))


def test_fact_equals_requires_a_value_parameter() -> None:
    """Le paramètre n'est exigé que si le fait existe : sinon, réponse `False`."""
    assert _run("fact_equals", {"field": "f"}, _context()) is False

    with pytest.raises(ValueError, match="manquant"):
        _run("fact_equals", {"field": "f"}, _context(facts={"f": True}))
