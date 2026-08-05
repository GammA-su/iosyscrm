# IOSYS Prospect CRM

CRM de prospection B2B : collecte SIRENE, enrichissement web, scoring, suivi
commercial. Spécification de référence : [`spec/`](spec/), conventions de
développement : [`AGENTS.md`](AGENTS.md).

## Prérequis

- Python 3.14 (l'image Docker utilise la même version mineure)
- [uv](https://docs.astral.sh/uv/)
- Docker Engine + Compose v2

## Démarrage

```bash
cp .env.example .env
cp docker-compose.override.yml.example docker-compose.override.yml
make install
make dev
curl -i localhost:8000/health
```

## Deux adresses de base de données

Il existe **deux** URL PostgreSQL distinctes, et elles ne doivent jamais
partager une variable :

| Contexte | Valeur | Origine |
| --- | --- | --- |
| Processus lancés depuis l'hôte (`make migrate`, `pytest`, `crm …`) | `postgresql+psycopg://crm:crm@localhost:5432/prospectcrm` | `DATABASE_URL` dans `.env` |
| Conteneurs `app` et `worker` | `postgresql+psycopg://crm:crm@postgres:5432/prospectcrm` | valeur **fixe** dans `docker-compose.yml` |

La valeur des conteneurs est écrite en dur, jamais interpolée. Un
`${DATABASE_URL:-postgresql+psycopg://…@postgres:…}` ne fonctionne pas :
Compose lit `.env` pour résoudre les interpolations, la variable est donc
toujours définie, la valeur par défaut n'est jamais utilisée, et les
conteneurs héritent de `@localhost` — où rien n'écoute dans leur espace de
noms réseau. Symptôme observé : `Connection refused` au démarrage de `app` et
`worker`.

La clé `environment:` de Compose est prioritaire sur `env_file:` : le
`DATABASE_URL` du `.env` est bien lu puis écrasé par la valeur du réseau
interne. Toutes les autres variables du `.env` passent normalement.

Vérification :

```bash
docker compose exec app printenv DATABASE_URL   # doit contenir @postgres
```

## Exposition du port PostgreSQL

`docker-compose.yml` ne publie **aucun** port sur le service `postgres` : en
production la base n'est joignable que depuis le réseau interne du compose.

Le poste de développement obtient `127.0.0.1:5432` en copiant
`docker-compose.override.yml.example` vers `docker-compose.override.yml`, que
Compose charge automatiquement. Ce fichier est ignoré par git : un serveur qui
ne l'a pas n'expose rien. C'est l'absence de la clé `ports` dans le fichier
principal qui constitue la garantie, pas un profil.

## Profils Compose

| Profil | Services |
| --- | --- |
| (aucun) | `postgres`, `app`, `worker` |
| `dev` | + `pgadmin` (127.0.0.1:5050) |
| `prod` | + `caddy` (80/443, TLS automatique) |

```bash
docker compose --profile dev up -d
docker compose --profile prod up -d
```

## Commandes

| Cible | Effet |
| --- | --- |
| `make install` | `uv sync` |
| `make dev` | compose, profil `dev` |
| `make migrate` | `alembic upgrade head` |
| `make revision m="…"` | nouvelle migration autogénérée |
| `make lint` | `ruff check` + `ruff format --check` |
| `make typecheck` | `mypy --strict app` |
| `make test` | `pytest` |
| `make check` | les trois précédentes |
| `make seed` | jeu de démonstration (à partir du lot T1) |

## Fins de ligne

Le dépôt force `eol=lf` via [`.gitattributes`](.gitattributes). Le
développement se fait sous Windows/WSL2, l'exécution sous Linux : un `Makefile`
ou un script en CRLF casse dans les conteneurs. Après modification du
`.gitattributes`, renormaliser avec :

```bash
git add --renormalize .
git status   # liste les fichiers effectivement convertis
```
