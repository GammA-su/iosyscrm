# AGENTS.md — Prospect CRM IOSYS

Fichier lu automatiquement par l'agent au démarrage de chaque session.
Il contient les règles permanentes. Le détail est dans `spec/`.

## Le projet

CRM de prospection B2B. Collecte quotidienne des entreprises nouvellement
immatriculées via l'API SIRENE de l'INSEE, enrichissement des données de
contact, scoring automatique, pipeline commercial.

Utilisateur unique au départ (IOSYS SAS, agence IT). Déploiement sur un
VPS unique via Docker Compose.

## Où trouver la spécification

| Fichier | Contenu |
|---|---|
| `spec/SPEC-0-preambule.md` | Mode d'emploi, périmètre exclu |
| `spec/SPEC-1-architecture.md` | Sections 1-2 : décisions techniques, arborescence |
| `spec/SPEC-2-schema.md` | Sections 3-4 : schéma SQL complet, configuration |
| `spec/SPEC-3-collecte.md` | Sections 5-7 : collecteur SIRENE, enrichissement, scoring |
| `spec/SPEC-4-conformite.md` | Sections 8-9 : RGPD, authentification |
| `spec/SPEC-5-api-ui.md` | Sections 10-12 : API, interface, ordonnanceur |
| `spec/SPEC-6-qualite.md` | Sections 13-16 : tests, déploiement, lots, acceptation |

La numérotation des sections est continue entre les fragments : un renvoi
« section 5.2 » désigne bien la section 5.2 de `SPEC-3-collecte.md`.

**Lis le ou les fragments concernés par le lot demandé avant d'écrire du
code. N'écris jamais de code sur la base d'un titre de section.**

## Règles permanentes

1. La spécification fait autorité. Si un choix te paraît mauvais, tu le
   signales en fin de réponse mais tu l'implémentes tel quel.
2. Le schéma de base de données (section 3) est figé. Aucune table,
   colonne, contrainte, index ou type ENUM ne doit être modifié, ajouté
   ou supprimé.
3. Les contrats d'API (section 10) sont figés.
4. Aucune dépendance hors de la liste de la section 1.4.
5. **Le code est synchrone.** Pas de `async def`, pas d'`await`, pas
   d'asyncpg, pas de SQLAlchemy async. Choix délibéré, justifié en
   section 1.2. Ne le remets pas en cause.
6. Découpage en couches strict : `api` → `services` → `repositories` →
   `models`. Un router n'importe jamais un modèle SQLAlchemy. Un
   repository ne contient aucune règle métier. Un service ne construit
   aucune requête SQL brute.
7. Code entièrement annoté. `ruff check`, `ruff format --check` et
   `mypy --strict app` doivent passer.
8. Les tests d'un lot s'écrivent avec le code de ce lot, pas après.
9. Tu n'implémentes que le lot demandé. Pas d'avance sur les lots
   suivants, pas de fichier vide « pour plus tard ».
10. Si une information métier manque, tu t'arrêtes et tu poses la
    question. Tu n'inventes aucun comportement.

## Pièges connus, à ne pas reproduire

- **SIRENE — pagination :** par curseur uniquement (`curseur=*`, puis
  `curseurSuivant`). Jamais `debut`/offset. `tri` est incompatible avec
  `curseur`.
- **SIRENE — filtre :** la requête filtre sur
  `dateDernierTraitementEtablissement`, pas sur la date de création.
  Beaucoup d'immatriculations arrivent en retard.
- **SIRENE — quota :** 30 requêtes/minute. Seau à jetons obligatoire.
- **Chargement des données :** `lazy="raise"` sur toutes les relations.
  Tout chargement doit être explicite. Les endpoints de liste ont des
  tests de comptage de requêtes SQL — les respecter.
- **Opposition RGPD :** vérifiée au moment de l'envoi, jamais à la mise
  en file. Les entrées `opt_outs` ne sont jamais purgées.
- **Ordonnanceur :** dans un conteneur dédié, jamais dans le processus
  API.
- **Front :** aucun CDN. Toutes les bibliothèques dans
  `app/static/vendor/`.

## Commandes

```bash
make install     # uv sync
make dev         # docker compose --profile dev up
make migrate     # alembic upgrade head
make test        # pytest
make lint        # ruff check + format --check
make typecheck   # mypy app
make check       # lint + typecheck + test
```

## Fin de réponse

Termine chaque réponse par :
- les fichiers créés ou modifiés
- les commandes de validation à lancer
- toute divergence par rapport à la spécification, avec sa justification
