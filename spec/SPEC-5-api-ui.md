# API, interface web, ordonnanceur

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 10. Contrats d'API

Préfixe `/api/v1`. Toutes les réponses sont des modèles Pydantic. Erreurs au format `{"detail": str, "code": str}`.

### 10.1 Entreprises et prospects

```
GET    /api/v1/companies
       ?q=&departement=&naf=&created_after=&created_before=
       &has_website=&min_score=&stage=&owner_id=
       &sort=score|date_creation|denomination &order=asc|desc
       &page=1&size=50
       -> Page[CompanyListItem]

GET    /api/v1/companies/{siren}            -> CompanyDetail
POST   /api/v1/companies/{siren}/enrich     -> EnrichmentRunOut (202)
GET    /api/v1/companies/{siren}/facts      -> list[FactOut]

POST   /api/v1/prospects                    body: {siren}          -> ProspectOut (201)
GET    /api/v1/prospects/{id}                                      -> ProspectDetail
PATCH  /api/v1/prospects/{id}   body: {stage_id?, owner_id?, priority?,
                                       notes?, next_action_at?,
                                       estimated_value_cents?, lost_reason?}
                                                                   -> ProspectOut
DELETE /api/v1/prospects/{id}                                      -> 204
GET    /api/v1/prospects/{id}/timeline                             -> list[TimelineEvent]
```

`GET /companies` doit rester sous 300 ms sur 500 000 lignes : une seule requête, jointure sur la vue `company_facts` et sur le dernier `score_snapshots` via `DISTINCT ON`. **Interdiction formelle de charger les faits en boucle par entreprise.**

### 10.2 Pipeline, tâches, emails, statistiques

```
GET    /api/v1/pipeline/board                          -> BoardOut (colonnes + cartes)
POST   /api/v1/pipeline/move  body: {prospect_id, to_stage_id, note?}
                                                       -> ProspectOut

GET    /api/v1/tasks?due_before=&user_id=&done=        -> list[TaskOut]
POST   /api/v1/tasks                                   -> TaskOut (201)
PATCH  /api/v1/tasks/{id}      body: {done?, due_at?, title?}   -> TaskOut

GET    /api/v1/email-templates                         -> list[TemplateOut]
POST   /api/v1/emails/preview  body: {prospect_id, template_id} -> EmailPreview
POST   /api/v1/emails/queue    body: {prospect_id, template_id, contact_id,
                                      scheduled_at?}   -> EmailOut (201)
POST   /api/v1/emails/{id}/cancel                      -> EmailOut

GET    /api/v1/stats/overview                          -> StatsOverview
GET    /api/v1/stats/funnel?since=                     -> FunnelOut
GET    /api/v1/stats/timeseries?metric=&granularity=   -> TimeSeriesOut

GET    /api/v1/admin/collector-runs                    -> list[RunOut]
POST   /api/v1/admin/collector/trigger                 -> RunOut (202)
GET    /api/v1/admin/scoring-rules                     -> list[RuleOut]
PATCH  /api/v1/admin/scoring-rules/{id}                -> RuleOut
POST   /api/v1/admin/rescore                           -> 202
GET    /api/v1/admin/opt-outs                          -> Page[OptOutOut]
POST   /api/v1/admin/opt-outs   body: {channel, value, reason}  -> 201
```

`StatsOverview` : total prospects, nouveaux cette semaine, nouveaux cette année, taux de conversion, nombre de devis, valeur estimée du pipeline, clients gagnés, prospects sans site, prospects avec site faible.

### 10.3 Routes publiques

```
GET  /desinscription/{token}     -> page de confirmation
POST /desinscription/{token}     -> enregistre l'opposition, page de confirmation
GET  /health                     -> {"status": "ok", "db": "ok", "version": "..."}
```

---

## 11. Interface web

### 11.1 Écrans

| Route | Contenu |
|---|---|
| `/` | Tableau de bord : indicateurs, tâches du jour, 20 meilleurs prospects, derniers runs |
| `/prospects` | Table filtrable, recherche instantanée, édition en ligne |
| `/prospects/{id}` | Fiche : identité SIRENE, faits enrichis avec source et date, contacts, historique, emails, tâches |
| `/pipeline` | Kanban 11 colonnes, glisser-déposer |
| `/stats` | 6 graphiques Chart.js |
| `/admin` | Règles de scoring, gabarits d'email, utilisateurs, runs, oppositions |
| `/login` | Connexion |

### 11.2 Règles d'implémentation

- Recherche instantanée : `hx-trigger="keyup changed delay:300ms"` vers un endpoint qui renvoie le fragment `partials/_prospect_rows.html`.
- Édition en ligne : `hx-patch` sur la cellule, retour du fragment de ligne mis à jour.
- Kanban : SortableJS, événement `onEnd` → `POST /api/v1/pipeline/move`, remise en place visuelle si la réponse est en erreur.
- Toute bibliothèque tierce est servie depuis `app/static/vendor/`. **Aucun CDN.**
- Chaque fait affiché montre au survol sa source et sa date de collecte. C'est ce qui rend l'outil crédible à l'usage.
- Badge visible sur les entreprises en diffusion partielle.

---

## 12. Ordonnanceur

Conteneur `worker` dédié, un seul exemplaire, APScheduler `BlockingScheduler`, fuseau `Europe/Paris`.

| Job | Fréquence | Rôle |
|---|---|---|
| `sirene_sync` | toutes les heures | Vérifie `dateDerniereMiseADisposition` ; sans changement, se termine sans appel supplémentaire |
| `enrich_batch` | toutes les 15 min | Traite `ENRICHMENT_BATCH_SIZE` entreprises selon la priorité de 6.6 |
| `rescore_all` | 03:00 | Recalcule tous les scores |
| `email_queue` | toutes les 5 min | Envoie les messages `queued` échus, en respectant 8.3 |
| `task_reminders` | 08:00 | Récapitulatif des tâches du jour par email interne |
| `purge_expired` | dimanche 04:00 | Purge RGPD |
| `session_cleanup` | 04:30 | Supprime les sessions expirées |

`max_instances=1` et `coalesce=True` sur chaque job. Un verrou consultatif PostgreSQL (`pg_try_advisory_lock`) protège `sirene_sync` et `email_queue` contre toute double exécution, y compris lors d'un redéploiement qui superposerait brièvement deux conteneurs.

---
