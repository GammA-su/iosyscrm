"""Calcul du score et empreinte du jeu de règles — sections 7.1 et 7.3."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from app.models.company import Company
from app.models.scoring import ScoringRule
from app.services.scoring.rules import resolve_predicate

#: Le score final est borné, quel que soit le cumul des règles (section 7.3).
MIN_SCORE: Final = 0
MAX_SCORE: Final = 100

#: `score_snapshots.ruleset_hash` est un CHAR(16).
RULESET_HASH_LENGTH: Final = 16


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """Règle active, détachée de l'ORM.

    Le moteur ne travaille que sur cette forme : il reste calculable et
    testable sans base, et l'empreinte porte exactement ces quatre champs.
    """

    key: str
    predicate: str
    params: dict[str, Any]
    points: int


@dataclass(frozen=True, slots=True)
class ScoringContext:
    """Tout ce dont les prédicats ont besoin, pour une entreprise.

    `facts` provient de la vue `company_facts` : une valeur par champ, la plus
    fiable puis la plus récente, les faits expirés étant déjà écartés.
    """

    company: Company
    facts: dict[str, Any] = field(default_factory=dict)
    contact_channels: frozenset[str] = frozenset()
    #: Date de référence de `age_days_lt`. Portée par le contexte pour que les
    #: prédicats restent des fonctions pures et le calcul reproductible.
    today: date = field(default_factory=lambda: datetime.now(tz=UTC).date())


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Score d'une entreprise, avec le détail des règles déclenchées."""

    score: int
    breakdown: dict[str, int]
    #: Au moins un fait valide. Permet de distinguer « pas de site » de
    #: « pas encore regardé » : sans cela, une entreprise jamais enrichie
    #: paraîtrait attirante par le seul jeu des règles d'absence.
    has_enrichment_data: bool


def compile_rules(rules: list[ScoringRule]) -> list[RuleSpec]:
    """Convertit les règles de la base en spécifications validées.

    C'est ici que le vocabulaire des prédicats est vérifié : un `predicate`
    inconnu lève `UnknownPredicateError` au chargement, jamais à l'usage.
    """
    specs: list[RuleSpec] = []
    for rule in rules:
        resolve_predicate(rule.predicate)
        specs.append(
            RuleSpec(
                key=rule.key,
                predicate=rule.predicate,
                params=dict(rule.params or {}),
                points=rule.points,
            )
        )
    return specs


def compute_score(context: ScoringContext, rules: list[RuleSpec]) -> ScoreResult:
    """Somme des points des règles vraies, bornée à [0, 100].

    `breakdown` ne contient que les règles déclenchées : c'est ce qui rend le
    score explicable dans l'interface, et donc digne de confiance.
    """
    breakdown: dict[str, int] = {}
    total = 0

    for rule in rules:
        predicate = resolve_predicate(rule.predicate)
        if predicate(context, rule.params):
            breakdown[rule.key] = rule.points
            total += rule.points

    return ScoreResult(
        score=min(max(total, MIN_SCORE), MAX_SCORE),
        breakdown=breakdown,
        has_enrichment_data=bool(context.facts),
    )


def ruleset_hash(rules: list[RuleSpec]) -> str:
    """Empreinte du jeu de règles actif, sur 16 caractères.

    Sérialisation canonique et triée : deux jeux identiques donnent la même
    empreinte quel que soit l'ordre de lecture. Désactiver une règle la retire
    du jeu actif, et change donc l'empreinte — c'est le but.
    """
    payload = [
        [rule.key, rule.predicate, rule.params, rule.points]
        for rule in sorted(rules, key=lambda spec: spec.key)
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:RULESET_HASH_LENGTH]
