# Décisions d'architecture, arborescence

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 1. Décisions d'architecture

### 1.1 Tableau des décisions

| Sujet | Décision | Raison |
|---|---|---|
| Langage | Python 3.13 | Écosystème, cohérence avec le reste de la stack |
| Gestionnaire de paquets | `uv` + `pyproject.toml` + `uv.lock` commité | Reproductibilité, vitesse |
| Framework web | FastAPI | Typage, OpenAPI généré, courbe connue |
| Mode d'exécution | **Synchrone** (endpoints `def`, pas `async def`) | Voir 1.2 — décision importante |
| ORM | SQLAlchemy 2.0, style `Mapped[...]` / `mapped_column` | Typage statique réel |
| Driver PostgreSQL | `psycopg` 3 (binaire) | Successeur maintenu de psycopg2 |
| Migrations | Alembic, autogenerate **relu à la main** | Autogenerate rate les index partiels |
| Base de données | PostgreSQL 17 | Extensions `pg_trgm`, `unaccent`, JSONB |
| Validation | Pydantic v2 + `pydantic-settings` | Contrats d'entrée/sortie et config |
| Client HTTP | `httpx` (client synchrone, connexions réutilisées) | Cohérent avec 1.2 |
| Parsing HTML | `selectolax` | 10× plus rapide que BeautifulSoup, suffisant ici |
| Templates | Jinja2 | Rendu serveur |
| Interactivité front | **HTMX 2 + Alpine.js 3**, pas de SPA | Voir 1.3 |
| CSS | Bootstrap 5.3, servi en local (pas de CDN) | Pas de dépendance réseau en prod |
| Kanban | SortableJS + endpoints HTMX | Drag & drop sans framework |
| Graphiques | Chart.js 4, servi en local | Suffisant pour 6 graphes |
| Ordonnancement | APScheduler dans un **conteneur séparé** | Voir 1.3 |
| Authentification | Session cookie + Argon2id, pas de JWT | Application monolithique, pas d'API publique |
| CLI | Typer | Tâches d'administration et jobs manuels |
| Logs | `structlog`, sortie JSON en prod, console en dev | Exploitable, corrélation par `run_id` |
| Tests | pytest + `respx` + `factory-boy` + `testcontainers` | Pas d'appel réseau réel en CI |
| Qualité | `ruff` (lint + format) + `mypy --strict` | Un seul outil pour lint et format |
| Reverse proxy | **Caddy**, pas nginx | TLS automatique, configuration de 6 lignes |
| Conteneurisation | Docker Compose, profils `dev` / `prod` | Un seul fichier, deux modes |

### 1.2 Justification : synchrone plutôt qu'asynchrone

C'est la décision qui va le plus à contre-courant, donc elle est justifiée explicitement.

L'API SIRENE est limitée à 30 requêtes par minute. La concurrence n'apporte donc **rien** sur la collecte : le goulot est le quota, pas la latence. Sur l'enrichissement, la concurrence est utile, mais elle est obtenue par un `ThreadPoolExecutor` de taille configurable, ce qui est trivial à écrire et à déboguer.

En échange, on élimine toute une classe de bugs : sessions SQLAlchemy async mal fermées, mélange de code bloquant dans une boucle d'événements, transactions qui fuient entre coroutines, pool de connexions saturé. Pour un projet maintenu par une personne, c'est le bon arbitrage.

FastAPI exécute les endpoints `def` dans un pool de threads : le débit reste largement suffisant pour un usage interne.

### 1.3 Justifications complémentaires

**HTMX plutôt qu'une SPA.** Le produit est une application interne à faible trafic, avec beaucoup de formulaires et de tableaux. Une SPA React imposerait un second build, un second système de types et une duplication des modèles. HTMX rend des fragments HTML depuis les mêmes templates Jinja2, ce qui divise par deux la surface de code.

**Scheduler dans un conteneur séparé.** Si APScheduler tourne dans le processus API et que l'API est lancée avec plusieurs workers Uvicorn, chaque worker déclenche le job : la collecte SIRENE s'exécute N fois en parallèle et brûle le quota. Un conteneur `worker` dédié, en un seul exemplaire, supprime le problème par construction.

**Caddy plutôt que nginx.** Le certificat TLS est obtenu et renouvelé automatiquement. Le `Caddyfile` fait six lignes contre une centaine pour un nginx équivalent correctement configuré.

### 1.4 Dépendances autorisées

Aucune autre dépendance ne doit être ajoutée sans validation explicite.

```toml
[project]
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14",
    "psycopg[binary]>=3.2",
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "httpx>=0.28",
    "selectolax>=0.3.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.20",
    "argon2-cffi>=23.1",
    "itsdangerous>=2.2",
    "apscheduler>=3.11",
    "typer>=0.15",
    "structlog>=24.4",
    "tenacity>=9.0",
    "email-validator>=2.2",
    "phonenumbers>=8.13",
    "python-dateutil>=2.9",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "respx>=0.22",
    "factory-boy>=3.3",
    "testcontainers[postgres]>=4.9",
    "ruff>=0.8",
    "mypy>=1.14",
    "types-python-dateutil",
]
```

---

## 2. Arborescence du dépôt

```
prospect-crm/
├── CAHIER-DES-CHARGES.md
├── README.md
├── Makefile
├── pyproject.toml
├── uv.lock
├── .env.example
├── .dockerignore
├── docker-compose.yml
├── Dockerfile
├── Caddyfile
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── main.py                  # création de l'app FastAPI, montage des routers
│   ├── cli.py                   # entrypoint Typer
│   ├── config.py                # Settings (pydantic-settings)
│   ├── logging.py               # configuration structlog
│   ├── deps.py                  # dépendances FastAPI (session, user courant)
│   ├── exceptions.py            # exceptions métier + handlers
│   ├── database/
│   │   ├── __init__.py
│   │   ├── engine.py            # engine + sessionmaker
│   │   └── base.py              # DeclarativeBase, mixins timestamps
│   ├── models/                  # SQLAlchemy — un fichier par domaine
│   │   ├── company.py
│   │   ├── prospect.py
│   │   ├── enrichment.py
│   │   ├── contact.py
│   │   ├── pipeline.py
│   │   ├── task.py
│   │   ├── email.py
│   │   ├── scoring.py
│   │   ├── collector.py
│   │   └── user.py
│   ├── schemas/                 # Pydantic — même découpage
│   ├── repositories/            # accès données, aucune logique métier
│   ├── services/                # logique métier
│   │   ├── sirene/
│   │   │   ├── client.py        # client HTTP bas niveau + quota
│   │   │   ├── parser.py        # réponse API -> dict normalisé
│   │   │   └── collector.py     # orchestration de la collecte
│   │   ├── enrichment/
│   │   │   ├── orchestrator.py
│   │   │   ├── providers/
│   │   │   │   ├── base.py
│   │   │   │   ├── societeinfo.py
│   │   │   │   ├── website_probe.py
│   │   │   │   └── contact_extract.py
│   │   │   └── normalize.py     # emails, téléphones, URLs
│   │   ├── scoring/
│   │   │   ├── engine.py
│   │   │   └── rules.py
│   │   ├── pipeline.py
│   │   ├── mailer.py
│   │   ├── compliance.py        # opt-out, purge, registre
│   │   └── auth.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── v1/                  # API JSON
│   │   │   ├── companies.py
│   │   │   ├── prospects.py
│   │   │   ├── pipeline.py
│   │   │   ├── tasks.py
│   │   │   ├── emails.py
│   │   │   ├── stats.py
│   │   │   └── admin.py
│   │   └── web/                 # routes HTML (Jinja2 + HTMX)
│   │       ├── auth.py
│   │       ├── dashboard.py
│   │       ├── prospects.py
│   │       ├── kanban.py
│   │       ├── stats.py
│   │       └── public.py        # désinscription (non authentifié)
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── main.py              # entrypoint du conteneur worker
│   │   └── jobs.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── partials/            # fragments HTMX
│   │   └── pages/
│   └── static/
│       ├── css/
│       ├── js/
│       └── vendor/              # bootstrap, htmx, alpine, sortable, chart.js
├── migrations/
│   ├── env.py
│   └── versions/
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   └── sirene/              # réponses API enregistrées (JSON)
    ├── unit/
    ├── integration/
    └── e2e/
```

**Règle de couches, non négociable :** `api` → `services` → `repositories` → `models`. Un router n'importe jamais un modèle SQLAlchemy directement. Un repository ne contient jamais de règle métier. Un service ne construit jamais de requête SQL.

---
