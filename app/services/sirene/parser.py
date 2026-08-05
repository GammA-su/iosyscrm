"""Traduction d'un établissement SIRENE en payload normalisé (section 5.3).

Le parser ne lève jamais sur une donnée absente : les unités en diffusion
partielle (`statutDiffusionEtablissement = 'P'`) ont des champs masqués et
doivent être conservées telles quelles, pas ignorées (section 3.2).
"""

import copy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

#: Code commune des DOM : trois caractères de département au lieu de deux.
DOM_PREFIX: Final = "97"

DEFAULT_STATUT_DIFFUSION: Final = "O"
PARTIAL_STATUT_DIFFUSION: Final = "P"

#: Catégorie juridique des entrepreneurs individuels. C'est le signal primaire
#: de personne physique : contrairement au nom patronymique, il n'est pas
#: masqué en diffusion partielle.
CATEGORIE_JURIDIQUE_PERSONNE_PHYSIQUE: Final = "1000"

#: Fuseau des horodatages de l'API. L'INSEE les transmet en heure locale et
#: sans indication de fuseau ; stockés tels quels, ils décaleraient d'une ou
#: deux heures selon la saison — et le watermark du collecteur avec eux.
SIRENE_TIMEZONE: Final = ZoneInfo("Europe/Paris")

#: Valeur signifiant « non renseigné » pour les tranches d'effectifs.
TRANCHE_EFFECTIFS_NON_RENSEIGNEE: Final = "NN"

#: Nomenclature d'activité en vigueur, portée par
#: `activitePrincipaleNAF25Etablissement`.
NOMENCLATURE_NAF25: Final = "NAF2025"

#: Clé ajoutée à `companies.raw` pour tracer la nomenclature retenue. Le
#: préfixe souligné la distingue des champs venant réellement de l'API.
NOMENCLATURE_RAW_KEY: Final = "_nomenclatureActivitePrincipaleRetenue"

#: Champs de `uniteLegale` retirés de `raw` avant stockage. Ce sont des données
#: personnelles sans finalité commerciale : le principe de minimisation de la
#: section 8 vaut aussi pour la conservation brute. `prenomUsuelUniteLegale`
#: est conservé, il compose `nom_complet`.
PERSONAL_UNITE_LEGALE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "sexeUniteLegale",
        "prenom1UniteLegale",
        "prenom2UniteLegale",
        "prenom3UniteLegale",
        "prenom4UniteLegale",
        "pseudonymeUniteLegale",
    }
)


@dataclass(frozen=True, slots=True)
class CompanyPayload:
    """Établissement siège normalisé, prêt pour la table `companies`.

    Tous les champs hors `siren`, `siret_siege` et `statut_diffusion` sont
    facultatifs : une unité en diffusion partielle n'en renseigne qu'une part.
    """

    siren: str
    siret_siege: str
    statut_diffusion: str
    denomination: str | None = None
    nom_complet: str | None = None
    is_personne_physique: bool = False
    categorie_juridique: str | None = None
    activite_principale: str | None = None
    tranche_effectifs: str | None = None
    date_creation: date | None = None
    etat_administratif: str | None = None
    adresse_numero: str | None = None
    adresse_type_voie: str | None = None
    adresse_libelle_voie: str | None = None
    adresse_complement: str | None = None
    code_postal: str | None = None
    commune: str | None = None
    code_commune: str | None = None
    departement: str | None = None
    date_dernier_traitement: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_diffusion_partielle(self) -> bool:
        """Unité à diffusion restreinte : à exclure de l'enrichissement (5.3)."""
        return self.statut_diffusion == PARTIAL_STATUT_DIFFUSION


def _clean(value: Any) -> str | None:
    """Chaîne non vide, débarrassée de ses espaces, sinon `None`."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_api_datetime(value: Any) -> datetime | None:
    """Horodatage de l'API, converti en `datetime` aware UTC.

    Une valeur sans fuseau est interprétée en heure de Paris, puis ramenée en
    UTC. Toutes les colonnes `TIMESTAMPTZ` du schéma reçoivent donc un instant
    non ambigu, quelle que soit la saison.
    """
    text = _clean(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SIRENE_TIMEZONE)
    return parsed.astimezone(UTC)


def _current_periode(raw: dict[str, Any]) -> dict[str, Any]:
    """Période en cours d'un établissement : celle sans `dateFin`.

    L'API renvoie les périodes de la plus récente à la plus ancienne ; à
    défaut de période ouverte, la première fait foi.
    """
    periodes = [item for item in raw.get("periodesEtablissement") or [] if isinstance(item, dict)]
    if not periodes:
        return {}
    for periode in periodes:
        if periode.get("dateFin") is None:
            return periode
    return periodes[0]


def sanitize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Copie de la réponse brute, expurgée des données personnelles.

    Retire de `uniteLegale` le sexe, les prénoms numérotés et le pseudonyme.
    Le dictionnaire d'origine n'est pas modifié : l'appelant garde sa réponse
    API intacte.
    """
    sanitized = copy.deepcopy(raw)
    unite = sanitized.get("uniteLegale")
    if isinstance(unite, dict):
        for personal_field in PERSONAL_UNITE_LEGALE_FIELDS:
            unite.pop(personal_field, None)
    return sanitized


def _tranche_effectifs(etablissement: Any, unite_legale: Any) -> str | None:
    """Tranche d'effectifs de l'établissement, à défaut celle de l'unité légale.

    « NN » signifie « non renseigné » et n'apporte rien de plus qu'une absence :
    la valeur est ramenée à `None` plutôt que stockée telle quelle.
    """
    for candidate in (etablissement, unite_legale):
        value = _clean(candidate)
        if value is not None and value != TRANCHE_EFFECTIFS_NON_RENSEIGNEE:
            return value
    return None


def departement_from_code_commune(code_commune: str | None) -> str | None:
    """Département d'un code commune : 2 caractères, 3 pour les DOM (5.3)."""
    code = _clean(code_commune)
    if code is None:
        return None
    return code[:3] if code.startswith(DOM_PREFIX) else code[:2]


def parse_etablissement(raw: dict[str, Any]) -> CompanyPayload:
    """Normalise un établissement de la réponse API."""
    unite = raw.get("uniteLegale") or {}
    adresse = raw.get("adresseEtablissement") or {}
    periode = _current_periode(raw)

    denomination = _clean(unite.get("denominationUniteLegale"))
    nom = _clean(unite.get("nomUniteLegale"))
    prenom = _clean(unite.get("prenomUsuelUniteLegale"))
    categorie_juridique = _clean(unite.get("categorieJuridiqueUniteLegale"))

    # Signal primaire : la catégorie juridique 1000 désigne un entrepreneur
    # individuel et reste lisible en diffusion partielle. Le nom patronymique
    # ne sert que si la catégorie juridique est absente — sinon toute unité en
    # statut 'P' passerait pour une personne morale, alors que ce sont
    # précisément celles à exclure de la prospection (section 8).
    if categorie_juridique is not None:
        is_personne_physique = categorie_juridique == CATEGORIE_JURIDIQUE_PERSONNE_PHYSIQUE
    else:
        is_personne_physique = denomination is None and (nom is not None or prenom is not None)

    nom_complet = " ".join(part for part in (nom, prenom) if part) if is_personne_physique else None

    code_commune = _clean(adresse.get("codeCommuneEtablissement"))

    # NAF 2025 est la nomenclature en vigueur ; le code porté par la période
    # courante est encore en NAFRev2 et ne sert que de repli. Les préfixes à
    # deux chiffres sont identiques dans les deux nomenclatures, seule la
    # lettre finale change : ni SIRENE_NAF_EXCLUDE ni la règle `target_sector`
    # n'ont à en tenir compte.
    activite_principale: str | None
    nomenclature: str | None
    activite_naf25 = _clean(raw.get("activitePrincipaleNAF25Etablissement"))
    if activite_naf25 is not None:
        activite_principale = activite_naf25
        nomenclature = NOMENCLATURE_NAF25
    else:
        activite_principale = _clean(periode.get("activitePrincipaleEtablissement"))
        nomenclature = _clean(periode.get("nomenclatureActivitePrincipaleEtablissement"))

    stored_raw = sanitize_raw(raw)
    stored_raw[NOMENCLATURE_RAW_KEY] = nomenclature

    return CompanyPayload(
        siren=_clean(raw.get("siren")) or "",
        siret_siege=_clean(raw.get("siret")) or "",
        statut_diffusion=_clean(raw.get("statutDiffusionEtablissement"))
        or DEFAULT_STATUT_DIFFUSION,
        denomination=denomination,
        nom_complet=nom_complet or None,
        is_personne_physique=is_personne_physique,
        categorie_juridique=categorie_juridique,
        activite_principale=activite_principale,
        tranche_effectifs=_tranche_effectifs(
            raw.get("trancheEffectifsEtablissement"),
            unite.get("trancheEffectifsUniteLegale"),
        ),
        date_creation=_parse_date(raw.get("dateCreationEtablissement")),
        etat_administratif=_clean(periode.get("etatAdministratifEtablissement")),
        adresse_numero=_clean(adresse.get("numeroVoieEtablissement")),
        adresse_type_voie=_clean(adresse.get("typeVoieEtablissement")),
        adresse_libelle_voie=_clean(adresse.get("libelleVoieEtablissement")),
        adresse_complement=_clean(adresse.get("complementAdresseEtablissement")),
        code_postal=_clean(adresse.get("codePostalEtablissement")),
        commune=_clean(adresse.get("libelleCommuneEtablissement")),
        code_commune=code_commune,
        departement=departement_from_code_commune(code_commune),
        date_dernier_traitement=parse_api_datetime(raw.get("dateDernierTraitementEtablissement")),
        raw=stored_raw,
    )
