# Tests, déploiement, lots, acceptation

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 13. Tests

### 13.1 Exigences

- **Aucun appel réseau réel.** `respx` intercepte tout `httpx`.
- Les réponses SIRENE sont des fixtures JSON enregistrées dans `tests/fixtures/sirene/` : page nominale, pagination sur trois pages, fin de curseur, réponse 429, réponse 404, unité en diffusion partielle, personne physique.
- PostgreSQL réel via `testcontainers` pour les tests d'intégration. Pas de SQLite : le code utilise JSONB, `DISTINCT ON` et des index partiels.
- Couverture minimale : **80 % sur `app/services/`**, 60 % global.

### 13.2 Scénarios obligatoires

**Collecteur** — pagination complète sur 3 pages ; arrêt correct quand `curseurSuivant == curseur` ; deux exécutions consécutives ne créent aucun doublon ; le watermark n'avance pas si le run échoue à mi-parcours ; un 429 déclenche un retry puis réussit ; une unité en diffusion partielle est stockée sans planter la contrainte de nom.

**Enrichissement** — un fait plus récent mais de confiance inférieure ne masque pas un fait fiable ; un fait expiré disparaît de `company_facts` ; `robots.txt` interdisant l'accès stoppe l'exploration.

**Conformité** — une adresse présente dans `opt_outs` n'est jamais envoyée, même si le message était déjà en file ; la purge ne supprime aucune opposition ; le pied de page est présent dans tout email rendu ; le quota journalier bloque le 81ᵉ envoi.

**Scoring** — une entreprise sans site obtient bien 30 points ; le score est borné à 100 ; `breakdown` liste exactement les règles déclenchées ; désactiver une règle change le `ruleset_hash`.

**Authentification** — une session expirée est refusée ; un cookie forgé est refusé ; un `viewer` reçoit 403 sur un `PATCH`.

---

## 14. Docker et déploiement

### 14.1 Services

| Service | Image | Profil |
|---|---|---|
| `postgres` | `postgres:17-alpine` | tous |
| `app` | build local | tous |
| `worker` | build local, commande `python -m app.scheduler.main` | tous |
| `caddy` | `caddy:2-alpine` | `prod` |
| `pgadmin` | `dpage/pgadmin4` | `dev` uniquement |

**`pgadmin` est strictement cantonné au profil `dev`.** Un pgAdmin exposé en production est une porte d'entrée sur la base.

Le port PostgreSQL n'est publié sur l'hôte que dans le profil `dev`. En production, la base n'est joignable que depuis le réseau interne du compose.

### 14.2 Dockerfile

Multi-étapes : étape `builder` avec `uv sync --frozen --no-dev`, étape finale `python:3.13-slim` avec l'environnement virtuel copié, utilisateur non root, `HEALTHCHECK` sur `/health`.

### 14.3 Makefile

```make
install     # uv sync
dev         # docker compose --profile dev up
migrate     # uv run alembic upgrade head
revision    # uv run alembic revision --autogenerate -m "$(m)"
test        # uv run pytest
lint        # uv run ruff check . && uv run ruff format --check .
typecheck   # uv run mypy app
check       # lint + typecheck + test
seed        # jeu de données de démonstration
```

### 14.4 Démarrage

```bash
cp .env.example .env          # renseigner SIRENE_API_KEY et APP_SECRET_KEY
make dev
make migrate
uv run crm users create --email you@iosys.fr --admin
uv run crm sirene backfill --days 30
# http://localhost:8000
```

### 14.5 Sauvegardes

Service `pg_dump` quotidien vers un volume distinct, rétention 14 jours, plus une copie hors machine. **Une restauration doit être testée avant la mise en production**, pas après le premier incident.

---

## 15. Lots de travail

Un prompt = un lot. Validation par `make check` entre chaque.

### T0 — Fondations
Squelette du dépôt, `pyproject.toml`, `Makefile`, `config.py` complet (toutes les variables de la section 4), `logging.py`, `database/`, Dockerfile, docker-compose avec profils, Caddyfile, `.env.example`, `/health`. Livrable : `make dev` démarre, `/health` répond.

### T1 — Schéma et migrations
Tous les modèles SQLAlchemy de la section 3, migration Alembic initiale, seed des `pipeline_stages` et des `scoring_rules`, vue `company_facts`. Livrable : `make migrate` applique le schéma exact de la section 3.

### T2 — Authentification
`services/auth.py`, modèles `users` / `sessions`, middleware, dépendances `current_user` et `require_role`, écran de connexion, commande `crm users create`. Livrable : les routes protégées renvoient 401 sans session.

### T3 — Client SIRENE
`services/sirene/client.py` (seau à jetons, retry, gestion des codes de la section 5.1) et `parser.py`. **Tests avec fixtures uniquement.** Livrable : les scénarios de pagination et de 429 passent.

### T4 — Collecteur
`services/sirene/collector.py` selon l'algorithme 5.2, watermarks, `collector_runs`, commandes CLI. Livrable : deux exécutions consécutives ne produisent aucun doublon.

### T5 — Enrichissement
Orchestrateur, `providers/` (societeinfo, website_probe, contact_extract), normalisation, écriture des faits avec TTL, `contacts`. Livrable : une entreprise avec site connu obtient un `website_quality_score` cohérent.

### T6 — Scoring
Moteur, prédicats de 7.2, `score_snapshots`, commande `crm score rebuild`. Livrable : `breakdown` explique le score règle par règle.

### T7 — API entreprises et prospects
Repositories et routes de 10.1, pagination, filtres, tri. Livrable : `GET /companies` avec filtres reste sous 300 ms sur 100 000 lignes injectées.

### T8 — Pipeline, tâches, statistiques
Routes de 10.2, `pipeline_events`, calculs d'agrégats. Livrable : un déplacement de carte crée l'événement correspondant.

### T9 — Emails et conformité
`services/mailer.py`, `services/compliance.py`, gabarits, garde-fous de 8.3, pied de page de 8.2, routes publiques de désinscription. Livrable : tous les tests de conformité de 13.2 passent, `MAIL_ENABLED=false` par défaut.

### T10 — Interface web
`base.html`, tableau de bord, liste, fiche, kanban, statistiques, administration. HTMX + Alpine + Bootstrap servis localement. Livrable : parcours complet utilisable sans toucher à l'API JSON.

### T11 — Ordonnanceur
Conteneur `worker`, jobs de la section 12, verrous consultatifs. Livrable : deux conteneurs `worker` lancés simultanément ne déclenchent qu'une seule collecte.

### T12 — Durcissement
Compléter la couverture aux seuils de 13.1, README d'exploitation, service de sauvegarde, procédure de restauration documentée et testée.

---

## 16. Critères d'acceptation de la v1

1. `make check` est vert.
2. Une collecte quotidienne remplit la base sans doublon et sans dépasser le quota de l'API.
3. Une entreprise sans site web apparaît dans les 20 premières du tableau de bord.
4. Chaque information enrichie affiche sa source et sa date.
5. Un clic sur le lien de désinscription empêche définitivement tout envoi ultérieur à cette adresse.
6. Aucun email ne peut partir tant que `MAIL_ENABLED` est à `false`.
7. Le kanban reflète l'état réel après rechargement de la page.
8. Une restauration de sauvegarde a été effectuée avec succès au moins une fois.
