"""Calcul du score et empreinte du jeu de règles — sections 7.1 et 7.3."""

from datetime import date
from typing import Any

import pytest

from app.models.company import Company
from app.models.scoring import ScoringRule
from app.services.scoring.engine import (
    RULESET_HASH_LENGTH,
    RuleSpec,
    ScoringContext,
    compile_rules,
    compute_score,
    ruleset_hash,
)
from app.services.scoring.rules import UnknownPredicateError

TODAY = date(2026, 8, 6)

#: Jeu de règles ACTIF de la section 7.3, à l'identique.
#: `local` et `recent` en sont sorties (migration 0008) : elles se
#: déclenchaient sur la quasi-totalité du portefeuille.
INITIAL_RULES: list[RuleSpec] = [
    RuleSpec("no_website", "fact_equals", {"field": "has_website", "value": False}, 30),
    RuleSpec("never_enriched", "fact_missing", {"field": "has_website"}, 5),
    RuleSpec("weak_website", "fact_lt", {"field": "website_quality_score", "value": 50}, 25),
    RuleSpec("no_https", "fact_equals", {"field": "website_https", "value": False}, 10),
    RuleSpec("not_responsive", "fact_equals", {"field": "website_responsive", "value": False}, 10),
    RuleSpec("very_recent", "age_days_lt", {"days": 30}, 10),
    RuleSpec("has_email", "has_contact", {"channel": "email"}, 15),
    RuleSpec("has_phone", "has_contact", {"channel": "phone"}, 10),
    RuleSpec(
        "target_sector",
        "naf_prefix_in",
        {"prefixes": ["41", "43", "68", "69", "70", "71", "96"]},
        10,
    ),
]


def _context(
    *,
    facts: dict[str, Any] | None = None,
    channels: frozenset[str] = frozenset(),
    date_creation: date = date(2020, 1, 1),
    activite: str | None = "62.01Z",
    departement: str | None = "75",
) -> ScoringContext:
    company = Company(
        id=1,
        siren="912345680",
        siret_siege="91234568000017",
        date_creation=date_creation,
        activite_principale=activite,
        departement=departement,
    )
    return ScoringContext(
        company=company, facts=facts or {}, contact_channels=channels, today=TODAY
    )


# --- Calcul -------------------------------------------------------------


def test_a_company_never_analysed_scores_five() -> None:
    """Aucun fait : la fiche entre dans la file d'attente, sans plus."""
    result = compute_score(_context(), INITIAL_RULES)

    assert result.score == 5
    assert result.breakdown == {"never_enriched": 5}
    assert result.has_enrichment_data is False


def test_a_confirmed_absence_of_website_scores_thirty() -> None:
    """Absence CONSTATÉE : le meilleur signal commercial du portefeuille."""
    result = compute_score(_context(facts={"has_website": False}), INITIAL_RULES)

    assert result.score == 30
    assert result.breakdown == {"no_website": 30}
    assert result.has_enrichment_data is True


def test_a_known_website_triggers_neither_rule() -> None:
    result = compute_score(
        _context(facts={"has_website": True, "website_url": "https://exemple.fr"}),
        INITIAL_RULES,
    )

    assert result.breakdown == {}
    assert result.score == 0
    assert result.has_enrichment_data is True


def test_the_two_absence_rules_are_mutually_exclusive() -> None:
    """`has_website` est soit absent, soit renseigné : jamais les deux."""
    for facts in ({}, {"has_website": False}, {"has_website": True}):
        breakdown = compute_score(_context(facts=facts), INITIAL_RULES).breakdown
        assert not {"no_website", "never_enriched"} <= set(breakdown)


def test_every_rule_firing_is_capped_at_one_hundred() -> None:
    """Le cumul brut vaut 120 : le score reste borné (section 7.3).

    `never_enriched` est exclu par construction : il exige l'absence du fait
    `has_website`, que `no_website` exige présent.
    """
    context = _context(
        facts={
            "has_website": False,
            "website_quality_score": 20,
            "website_https": False,
            "website_responsive": False,
        },
        channels=frozenset({"email", "phone"}),
        date_creation=date(2026, 7, 25),
        activite="43.32A",
        departement="68",
    )

    result = compute_score(context, INITIAL_RULES)

    assert sum(result.breakdown.values()) == 120
    assert result.score == 100
    assert set(result.breakdown) == {
        rule.key for rule in INITIAL_RULES if rule.key != "never_enriched"
    }


def test_breakdown_lists_exactly_the_triggered_rules() -> None:
    context = _context(
        facts={
            "has_website": True,
            "website_url": "https://exemple.fr",
            "website_https": False,
        },
        channels=frozenset({"email"}),
        date_creation=date(2026, 6, 1),
        departement="68",
    )

    result = compute_score(context, INITIAL_RULES)

    assert result.breakdown == {"no_https": 10, "has_email": 15}
    assert result.score == 25


def test_an_empty_ruleset_scores_zero() -> None:
    result = compute_score(_context(), [])

    assert result.score == 0
    assert result.breakdown == {}


# --- Empreinte du jeu de règles ----------------------------------------


def test_ruleset_hash_is_sixteen_characters() -> None:
    digest = ruleset_hash(INITIAL_RULES)

    assert len(digest) == RULESET_HASH_LENGTH
    assert digest == ruleset_hash(INITIAL_RULES)


def test_ruleset_hash_ignores_the_order_of_the_rules() -> None:
    assert ruleset_hash(INITIAL_RULES) == ruleset_hash(list(reversed(INITIAL_RULES)))


def test_deactivating_a_rule_changes_the_hash() -> None:
    """Une règle désactivée sort du jeu actif : l'empreinte doit bouger."""
    without_email = [rule for rule in INITIAL_RULES if rule.key != "has_email"]

    assert ruleset_hash(without_email) != ruleset_hash(INITIAL_RULES)


@pytest.mark.parametrize(
    "changed",
    [
        RuleSpec("has_email", "has_contact", {"channel": "email"}, 20),
        RuleSpec("has_email", "has_contact", {"channel": "phone"}, 15),
        RuleSpec("has_email", "fact_missing", {"channel": "email"}, 15),
        RuleSpec("courriel", "has_contact", {"channel": "email"}, 15),
    ],
)
def test_any_change_to_a_rule_changes_the_hash(changed: RuleSpec) -> None:
    modified = [rule for rule in INITIAL_RULES if rule.key != "has_email"] + [changed]

    assert ruleset_hash(modified) != ruleset_hash(INITIAL_RULES)


# --- Chargement ---------------------------------------------------------


def test_compile_rules_rejects_an_unknown_predicate() -> None:
    rule = ScoringRule(
        key="exotique", label="Règle exotique", predicate="fact_between", params={}, points=5
    )

    with pytest.raises(UnknownPredicateError, match="fact_between"):
        compile_rules([rule])


def test_compile_rules_copies_the_parameters() -> None:
    params = {"field": "has_website"}
    rule = ScoringRule(
        key="never_enriched",
        label="Jamais analysée",
        predicate="fact_missing",
        params=params,
        points=5,
    )

    specs = compile_rules([rule])
    params["field"] = "autre"

    assert specs[0].params == {"field": "has_website"}
