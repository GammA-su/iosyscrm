# Schéma de base de données, configuration

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 3. Schéma de base de données

Schéma à jour au lot T4-bis (migration 0006).

DDL de référence. Alembic doit produire exactement ce schéma.

### 3.1 Extensions et types

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TYPE contact_channel AS ENUM ('email', 'phone');
CREATE TYPE contact_origin  AS ENUM ('societeinfo', 'website', 'manual', 'sirene');
CREATE TYPE email_status    AS ENUM ('draft', 'queued', 'sent', 'failed', 'bounced', 'cancelled');
CREATE TYPE run_status      AS ENUM ('running', 'success', 'partial', 'failed');
CREATE TYPE user_role       AS ENUM ('admin', 'commercial', 'viewer');
```

### 3.2 Référentiel SIRENE

`companies` est un **miroir fidèle** de SIRENE. Elle ne contient aucune donnée commerciale : elle peut être resynchronisée intégralement sans risque.

```sql
CREATE TABLE companies (
    id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    siren                    CHAR(9)      NOT NULL UNIQUE,
    siret_siege              CHAR(14)     NOT NULL,
    denomination             TEXT,                 -- personnes morales
    nom_complet              TEXT,                 -- personnes physiques (nom + prénom usuel)
    is_personne_physique     BOOLEAN      NOT NULL DEFAULT FALSE,
    categorie_juridique      VARCHAR(4),
    activite_principale      VARCHAR(6),           -- code NAF, ex. 62.01Z
    tranche_effectifs        VARCHAR(2),
    date_creation            DATE         NOT NULL,
    etat_administratif       CHAR(1)      NOT NULL, -- A active, F fermée
    statut_diffusion         CHAR(1)      NOT NULL DEFAULT 'O', -- O ouverte, P partielle
    adresse_numero           VARCHAR(10),
    adresse_type_voie        VARCHAR(20),
    adresse_libelle_voie     TEXT,
    adresse_complement       TEXT,
    code_postal              VARCHAR(5),
    commune                  TEXT,
    code_commune             VARCHAR(5),
    departement              VARCHAR(3),
    date_dernier_traitement  TIMESTAMPTZ,
    first_seen_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_synced_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    raw                      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT companies_name_present CHECK (
        denomination IS NOT NULL OR nom_complet IS NOT NULL OR statut_diffusion = 'P'
    )
);

CREATE INDEX idx_companies_date_creation     ON companies (date_creation DESC);
CREATE INDEX idx_companies_departement       ON companies (departement);
CREATE INDEX idx_companies_naf               ON companies (activite_principale);
CREATE INDEX idx_companies_dernier_traitement ON companies (date_dernier_traitement DESC);
CREATE INDEX idx_companies_denomination_trgm ON companies
    USING gin (coalesce(denomination, nom_complet) gin_trgm_ops);
CREATE INDEX idx_companies_actives           ON companies (date_creation DESC)
    WHERE etat_administratif = 'A';
CREATE INDEX idx_companies_prospectables     ON companies (date_creation DESC)
    WHERE etat_administratif = 'A' AND statut_diffusion = 'O';
```

**Décisions justifiées :**

- Une ligne par **SIREN**, sur l'établissement **siège** uniquement. Les établissements secondaires n'ont pas d'intérêt en prospection et multiplieraient le volume par trois.
- `raw` conserve la réponse API brute. Coût de stockage négligeable, valeur inestimable quand on découvre six mois plus tard qu'un champ a été mal mappé.
- `statut_diffusion = 'P'` (diffusion partielle) doit être **conservé mais marqué**, pas ignoré : ces unités existent, elles ont juste des champs masqués. La contrainte `companies_name_present` en tient compte.

### 3.3 État commercial

```sql
CREATE TABLE pipeline_stages (
    id        SMALLINT PRIMARY KEY,
    key       VARCHAR(32)  NOT NULL UNIQUE,
    label     TEXT         NOT NULL,
    position  SMALLINT     NOT NULL,
    color     VARCHAR(7)   NOT NULL DEFAULT '#6c757d',
    is_won    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_lost   BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE prospects (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id     BIGINT       NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    stage_id       SMALLINT     NOT NULL REFERENCES pipeline_stages(id),
    owner_id       BIGINT       REFERENCES users(id) ON DELETE SET NULL,
    priority       SMALLINT     NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    notes          TEXT         NOT NULL DEFAULT '',
    next_action_at TIMESTAMPTZ,
    estimated_value_cents BIGINT,
    lost_reason    TEXT,
    entered_stage_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_prospects_stage      ON prospects (stage_id);
CREATE INDEX idx_prospects_owner      ON prospects (owner_id);
CREATE INDEX idx_prospects_next_action ON prospects (next_action_at)
    WHERE next_action_at IS NOT NULL;

CREATE TABLE pipeline_events (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prospect_id   BIGINT      NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    from_stage_id SMALLINT    REFERENCES pipeline_stages(id),
    to_stage_id   SMALLINT    NOT NULL REFERENCES pipeline_stages(id),
    user_id       BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_pipeline_events_prospect ON pipeline_events (prospect_id, created_at DESC);
```

**Décision : séparation `companies` / `prospects`.** Un prospect est créé paresseusement, à la première action commerciale ou à l'entrée en pipeline. Cela permet de resynchroniser ou de purger le référentiel sans jamais toucher au travail commercial, et évite d'avoir 400 000 lignes de pipeline vides.

Contenu initial de `pipeline_stages` (seed de migration) :

| id | key | label | position | is_won | is_lost |
|---|---|---|---|---|---|
| 1 | `nouveau` | Nouveau | 1 | f | f |
| 2 | `a_contacter` | À contacter | 2 | f | f |
| 3 | `email_envoye` | Email envoyé | 3 | f | f |
| 4 | `appele` | Appelé | 4 | f | f |
| 5 | `relance_1` | Relance 1 | 5 | f | f |
| 6 | `relance_2` | Relance 2 | 6 | f | f |
| 7 | `rendez_vous` | Rendez-vous | 7 | f | f |
| 8 | `devis_envoye` | Devis envoyé | 8 | f | f |
| 9 | `negociation` | Négociation | 9 | f | f |
| 10 | `gagne` | Client gagné | 10 | **t** | f |
| 11 | `perdu` | Perdu | 11 | f | **t** |

### 3.4 Enrichissement

```sql
CREATE TABLE enrichment_facts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id   BIGINT       NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    field        VARCHAR(48)  NOT NULL,
    value        TEXT,
    value_json   JSONB,
    source       VARCHAR(32)  NOT NULL,
    confidence   NUMERIC(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    collected_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ  NOT NULL,
    CONSTRAINT uq_fact UNIQUE (company_id, field, source)
);

CREATE INDEX idx_facts_company    ON enrichment_facts (company_id);
CREATE INDEX idx_facts_field      ON enrichment_facts (field, value);
CREATE INDEX idx_facts_expiration ON enrichment_facts (expires_at);

CREATE TABLE enrichment_runs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id   BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    provider     VARCHAR(32) NOT NULL,
    status       run_status  NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    facts_written SMALLINT   NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE INDEX idx_enrichment_runs_company ON enrichment_runs (company_id, started_at DESC);
```

**Décision : table de faits générique plutôt que colonnes larges.** Chaque information enrichie porte sa source, sa date et sa confiance. Sans cela, au bout de six mois, personne ne sait si `email = contact@x.fr` vient d'une API payante fiable ou d'une expression régulière hasardeuse sur une page d'accueil — et la base devient inutilisable.

Vocabulaire fermé de `field` (aucun autre autorisé) :

| field | type | exemple |
|---|---|---|
| `website_url` | texte | `https://exemple.fr` |
| `has_website` | booléen | `true` |
| `website_https` | booléen | `true` |
| `website_responsive` | booléen | `false` |
| `website_cms` | texte | `wordpress` |
| `website_copyright_year` | entier | `2017` |
| `website_ttfb_ms` | entier | `840` |
| `website_status_code` | entier | `200` |
| `website_quality_score` | entier 0-100 | `35` |
| `email_domain_professional` | booléen | `false` (gmail/orange/free → false) |
| `effectif_estime` | entier | `3` |
| `dirigeant_present` | booléen | `true` |

La vue de lecture retient, pour chaque `(company_id, field)`, la valeur de plus haute confiance, puis la plus récente :

```sql
CREATE VIEW company_facts AS
SELECT DISTINCT ON (company_id, field)
       company_id, field, value, value_json, source, confidence, collected_at
FROM enrichment_facts
WHERE expires_at > now()
ORDER BY company_id, field, confidence DESC, collected_at DESC;
```

### 3.5 Contacts et conformité

```sql
CREATE TABLE contacts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id    BIGINT          NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    channel       contact_channel NOT NULL,
    value         TEXT            NOT NULL,      -- forme normalisée
    display_value TEXT            NOT NULL,      -- forme d'origine
    origin        contact_origin  NOT NULL,
    confidence    NUMERIC(3,2)    NOT NULL,
    is_generic    BOOLEAN         NOT NULL DEFAULT FALSE, -- contact@, info@
    is_primary    BOOLEAN         NOT NULL DEFAULT FALSE,
    verified_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT uq_contact UNIQUE (company_id, channel, value),
    CONSTRAINT contacts_confidence_check CHECK (confidence BETWEEN 0 AND 1)
);

CREATE UNIQUE INDEX idx_contact_primary ON contacts (company_id, channel)
    WHERE is_primary;

-- Liste de suppression globale. Ne référence PAS company_id :
-- une opposition vaut pour l'adresse, pas pour une fiche.
CREATE TABLE opt_outs (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel    contact_channel NOT NULL,
    value_hash CHAR(64)        NOT NULL,  -- sha256 de la valeur normalisée
    reason     TEXT,
    source     VARCHAR(32)     NOT NULL,  -- 'unsubscribe_link', 'manual', 'bounce'
    created_at TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT uq_optout UNIQUE (channel, value_hash)
);
```

**Décision : `opt_outs` stocke un hachage, pas la valeur en clair, et n'a pas de clé étrangère.** Si l'entreprise est purgée de la base, l'opposition doit survivre — sinon un contact désinscrit sera re-sollicité au prochain cycle de collecte. C'est l'erreur classique, et elle est sanctionnable.

### 3.6 Emails et tâches

```sql
CREATE TABLE email_templates (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key        VARCHAR(48)  NOT NULL UNIQUE,
    label      TEXT         NOT NULL,
    subject    TEXT         NOT NULL,
    body       TEXT         NOT NULL,          -- Jinja2
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE email_messages (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prospect_id         BIGINT       NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    contact_id          BIGINT       REFERENCES contacts(id) ON DELETE SET NULL,
    template_id         BIGINT       REFERENCES email_templates(id) ON DELETE SET NULL,
    to_address          TEXT         NOT NULL,
    subject             TEXT         NOT NULL,
    body                TEXT         NOT NULL,   -- corps final rendu
    status              email_status NOT NULL DEFAULT 'draft',
    unsubscribe_token   CHAR(43)     NOT NULL UNIQUE,  -- token URL-safe 32 octets
    scheduled_at        TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ,
    error               TEXT,
    created_by          BIGINT       REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_email_queue ON email_messages (scheduled_at)
    WHERE status = 'queued';
CREATE INDEX idx_email_prospect ON email_messages (prospect_id, created_at DESC);

CREATE TABLE tasks (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prospect_id BIGINT      NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    user_id     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    title       TEXT        NOT NULL,
    kind        VARCHAR(16) NOT NULL DEFAULT 'rappel', -- rappel, appel, email, rdv
    due_at      TIMESTAMPTZ NOT NULL,
    done_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_due ON tasks (due_at) WHERE done_at IS NULL;
```

**Le corps final est stocké**, pas seulement l'identifiant du gabarit. Un gabarit modifié six mois plus tard ne doit pas réécrire l'historique de ce qui a réellement été envoyé.

### 3.7 Scoring

```sql
CREATE TABLE scoring_rules (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key        VARCHAR(48) NOT NULL UNIQUE,
    label      TEXT        NOT NULL,
    predicate  VARCHAR(24) NOT NULL,  -- voir 7.2
    params     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    points     SMALLINT    NOT NULL,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE score_snapshots (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id  BIGINT      NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    score       SMALLINT    NOT NULL,
    breakdown   JSONB       NOT NULL,   -- {"no_website": 30, "recent": 20}
    ruleset_hash CHAR(16)   NOT NULL,   -- empreinte du jeu de règles actif
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scores_latest ON score_snapshots (company_id, computed_at DESC);
CREATE INDEX idx_scores_ranking ON score_snapshots (score DESC, computed_at DESC);
```

**Décision : historiser les scores plutôt que les écraser.** Dans six mois, croiser `score_snapshots` avec `pipeline_events` permet de recalibrer les pondérations sur des données réelles au lieu d'une intuition. Le coût aujourd'hui est de deux colonnes ; le refaire plus tard coûte une reprise de schéma et des données perdues à jamais.

### 3.8 Collecte et utilisateurs

```sql
CREATE TABLE collector_watermarks (
    key        VARCHAR(64) PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE collector_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind           VARCHAR(32) NOT NULL,
    status         run_status  NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    records_seen   INTEGER     NOT NULL DEFAULT 0,
    records_new    INTEGER     NOT NULL DEFAULT 0,
    records_updated INTEGER    NOT NULL DEFAULT 0,
    records_rejected INTEGER   NOT NULL DEFAULT 0,
    api_calls      INTEGER     NOT NULL DEFAULT 0,
    window_start   TIMESTAMPTZ,
    window_end     TIMESTAMPTZ,
    error          TEXT
);

CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    full_name     TEXT        NOT NULL,
    role          user_role   NOT NULL DEFAULT 'commercial',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash CHAR(64)    NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    ip         INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_expiry ON sessions (expires_at);
```

---

## 4. Configuration

`.env.example` exhaustif. Toute variable est déclarée dans `app/config.py` via `pydantic-settings`, avec un type et une valeur par défaut sûre. **Aucun `os.getenv` ailleurs dans le code.**

```env
# --- Application ---
APP_ENV=development                 # development | production
APP_SECRET_KEY=                     # 64 caractères, obligatoire en production
APP_BASE_URL=http://localhost:8000
APP_LOG_LEVEL=INFO

# --- Base de données ---
DATABASE_URL=postgresql+psycopg://crm:crm@localhost:5432/prospectcrm
DATABASE_POOL_SIZE=10
DATABASE_ECHO=false

# --- API SIRENE (INSEE) ---
SIRENE_API_KEY=
SIRENE_BASE_URL=https://api.insee.fr/api-sirene/3.11
SIRENE_RATE_LIMIT_PER_MINUTE=28     # marge sous le quota de 30
SIRENE_PAGE_SIZE=1000
SIRENE_LOOKBACK_DAYS=30             # fenêtre de dates de création retenue
SIRENE_DEPARTEMENTS=68,67,90,88     # vide = toute la France
SIRENE_NAF_EXCLUDE=84,85,86,87,88   # administrations, santé, enseignement

# --- Enrichissement ---
ENRICHMENT_ENABLED=true
ENRICHMENT_BATCH_SIZE=50
ENRICHMENT_MAX_WORKERS=8
ENRICHMENT_USER_AGENT=IOSYS-ProspectBot/1.0 (+https://iosys.fr/bot)
ENRICHMENT_TIMEOUT_SECONDS=10
ENRICHMENT_TTL_DAYS=90
ENRICHMENT_RESPECT_ROBOTS=true

SOCIETEINFO_ENABLED=true
SOCIETEINFO_API_KEY=
SOCIETEINFO_BASE_URL=

# --- Email ---
MAIL_ENABLED=false                  # false = mode simulation, rien n'est envoyé
MAIL_SMTP_HOST=
MAIL_SMTP_PORT=587
MAIL_SMTP_USER=
MAIL_SMTP_PASSWORD=
MAIL_FROM_ADDRESS=
MAIL_FROM_NAME=IOSYS
MAIL_REPLY_TO=
MAIL_DAILY_LIMIT=80
MAIL_MIN_INTERVAL_SECONDS=45

# --- Conformité ---
COMPLIANCE_RETENTION_DAYS=1095      # 3 ans après le dernier contact
COMPLIANCE_CONTROLLER_NAME=IOSYS SAS
COMPLIANCE_CONTROLLER_ADDRESS=
COMPLIANCE_CONTACT_EMAIL=

# --- Ordonnanceur ---
SCHEDULER_ENABLED=true
SCHEDULER_TIMEZONE=Europe/Paris
```

**`MAIL_ENABLED=false` par défaut** : le premier lancement ne peut pas envoyer d'email par accident. En mode simulation, les messages passent en statut `sent` avec un marqueur, et le corps est écrit dans les logs.

---
