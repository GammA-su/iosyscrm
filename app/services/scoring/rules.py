"""Prédicats de scoring — tableau 7.2 du cahier des charges.

Un prédicat est une fonction pure : il ne lit que le contexte qu'on lui donne,
n'interroge rien et n'a aucun effet de bord. Le registre `PREDICATES` couvre
exactement les neuf prédicats du tableau, ni plus ni moins — un `predicate`
inconnu en base est une erreur, pas une règle qu'on ignore en silence.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from app.services.scoring.engine import ScoringContext

PredicateFn = Callable[["ScoringContext", dict[str, Any]], bool]


class UnknownPredicateError(ValueError):
    """`scoring_rules.predicate` ne correspond à aucun prédicat de la section 7.2."""


def _required(params: dict[str, Any], key: str) -> Any:
    if key not in params:
        raise ValueError(f"Paramètre « {key} » manquant pour ce prédicat.")
    return params[key]


def _strict_equals(left: Any, right: Any) -> bool:
    """Égalité qui ne confond pas `False` avec `0` ni `True` avec `1`."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _as_number(value: Any) -> float | None:
    """Valeur numérique exploitable, ou `None`. Un booléen n'est pas un nombre."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def fact_missing(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Aucun fait valide pour ce champ.

    Un champ absent de `company_facts` l'est pour l'une de ces deux raisons :
    l'entreprise n'a jamais été enrichie, ou le fait a expiré. Les deux se
    traitent de la même façon : l'information n'est pas disponible.
    """
    return _required(params, "field") not in context.facts


def fact_equals(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Le fait existe et vaut exactement la valeur attendue."""
    field = _required(params, "field")
    if field not in context.facts:
        return False
    return _strict_equals(context.facts[field], _required(params, "value"))


def fact_lt(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Le fait existe, est numérique, et est strictement inférieur au seuil."""
    value = _as_number(context.facts.get(_required(params, "field")))
    threshold = _as_number(_required(params, "value"))
    if value is None or threshold is None:
        return False
    return value < threshold


def fact_gt(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Le fait existe, est numérique, et est strictement supérieur au seuil."""
    value = _as_number(context.facts.get(_required(params, "field")))
    threshold = _as_number(_required(params, "value"))
    if value is None or threshold is None:
        return False
    return value > threshold


def age_days_lt(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """L'entreprise a été créée il y a moins de N jours."""
    days = _as_number(_required(params, "days"))
    if days is None or context.company.date_creation is None:
        return False
    return (context.today - context.company.date_creation).days < days


def naf_prefix_in(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Le code d'activité commence par l'un des préfixes.

    Les préfixes à deux chiffres sont identiques en NAFRev2 et en NAF 2025 :
    la règle est insensible au changement de nomenclature.
    """
    activity = context.company.activite_principale
    if activity is None:
        return False
    prefixes = _required(params, "prefixes")
    return any(activity.startswith(prefix) for prefix in prefixes)


def departement_in(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """Le département fait partie de la liste."""
    departement = context.company.departement
    return departement is not None and departement in set(_required(params, "codes"))


def has_contact(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """L'entreprise porte au moins un contact de ce canal."""
    return _required(params, "channel") in context.contact_channels


def legal_form_in(context: "ScoringContext", params: dict[str, Any]) -> bool:
    """La catégorie juridique fait partie de la liste."""
    categorie = context.company.categorie_juridique
    return categorie is not None and categorie in set(_required(params, "codes"))


#: Les neuf prédicats de la section 7.2, et eux seuls.
PREDICATES: Final[dict[str, PredicateFn]] = {
    "fact_missing": fact_missing,
    "fact_equals": fact_equals,
    "fact_lt": fact_lt,
    "fact_gt": fact_gt,
    "age_days_lt": age_days_lt,
    "naf_prefix_in": naf_prefix_in,
    "departement_in": departement_in,
    "has_contact": has_contact,
    "legal_form_in": legal_form_in,
}


def resolve_predicate(name: str) -> PredicateFn:
    """Prédicat portant ce nom, ou `UnknownPredicateError`."""
    try:
        return PREDICATES[name]
    except KeyError:
        raise UnknownPredicateError(
            f"Prédicat de scoring inconnu : {name!r}. "
            f"Prédicats disponibles (section 7.2) : {', '.join(sorted(PREDICATES))}."
        ) from None
