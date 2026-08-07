# Collecteur SIRENE, enrichissement, scoring

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 5. Collecteur SIRENE

### 5.1 Contraintes de l'API à respecter impérativement

1. **Quota de 30 requêtes par minute.** Le client implémente un seau à jetons à 28/min (marge de sécurité), partagé par tout le processus.
2. **Pagination par curseur, jamais par offset.** Première requête avec `curseur=*` ; les suivantes réinjectent `curseurSuivant`. La boucle s'arrête quand `curseurSuivant == curseur` envoyé.
3. **Ne jamais combiner `tri` et `curseur`.** Le tri par date de création se fait côté base, pas côté API.
4. **Taille de page maximale : 1000** (`nombre=1000`).
5. **En-tête d'authentification : `X-INSEE-Api-Key-Integration`.**
6. **Paramètre `champs`** pour limiter la charge utile aux colonnes réellement stockées.
7. **Codes de retour à gérer distinctement :** 200, 301 (SIREN remplacé, suivre le lien de succession), 400 (requête malformée — arrêt et alerte, pas de retry), 401 (clé invalide — arrêt), 404 (aucun résultat, ce n'est pas une erreur), 414 (URL trop longue — réduire la requête), 429 (quota — attente puis retry), 5xx (retry).

### 5.2 Algorithme de synchronisation

Point clé, souvent manqué : **on ne filtre pas sur la date de création pour interroger l'API, on filtre sur la date de dernier traitement.** Beaucoup d'immatriculations sont enregistrées avec plusieurs jours de retard ; une requête sur « créées ces dernières 24 h » les perd définitivement.

```
1. Appeler le service `informations` de l'API pour lire
   `dateDerniereMiseADisposition`.
2. Comparer avec le watermark `sirene.last_disposition`.
   Si identique -> aucune nouvelle donnée, terminer en statut `success`
   sans consommer de quota. (C'est pour cela que le job tourne toutes
   les heures : il est presque toujours gratuit.)
3. Ouvrir un `collector_runs` en statut `running`.
4. Fenêtre = [watermark `sirene.last_traitement` - 1 jour, maintenant].
   Le recouvrement d'un jour absorbe les effets de bord de fuseau.
5. Construire la requête :
      q = periode(etatAdministratifEtablissement:A)
          AND etablissementSiege:true
          AND dateDernierTraitementEtablissement:[<debut> TO <fin>]
          AND dateCreationEtablissement:[<aujourdhui - LOOKBACK_DAYS> TO <aujourdhui>]
          [AND (codeCommuneEtablissement:68* OR ...)]   si SIRENE_DEPARTEMENTS
      nombre = 1000
      curseur = *
6. Boucler :
      - respecter le seau à jetons avant chaque appel
      - retry avec backoff exponentiel + jitter sur 429 et 5xx (5 tentatives)
      - parser, normaliser, filtrer les NAF exclus
      - UPSERT par lot de 500 (ON CONFLICT (siren) DO UPDATE)
      - incrémenter les compteurs du run
      - arrêt quand curseurSuivant == curseur
7. Écrire les watermarks `sirene.last_traitement` et
   `sirene.last_disposition` DANS LA MÊME TRANSACTION que le dernier lot.
8. Clôturer le run en `success`. En cas d'exception, clôturer en
   `failed` avec la trace, sans écrire le watermark.
```

L'idempotence vient de l'`UPSERT` sur `siren` **et** du watermark écrit transactionnellement. Une contrainte d'unicité seule ne suffit pas : elle empêche les doublons mais ne dit pas où reprendre.

### 5.3 Normalisation

- `denomination` pour les personnes morales ; pour les personnes physiques, concaténer nom et prénom usuel et positionner `is_personne_physique = true`.
- `departement` : deux premiers caractères du code commune, trois pour les DOM (97x).
- Les unités en diffusion partielle (`statut_diffusion = 'P'`) ont des champs d'adresse et de dénomination absents. Elles sont **stockées telles quelles**, avec un badge dans l'interface, et **exclues de l'enrichissement automatique**.

### 5.4 Commandes CLI

```bash
uv run crm sirene sync                    # synchronisation incrémentale
uv run crm sirene backfill --days 90      # rattrapage, ignore le watermark
uv run crm sirene status                  # watermarks + 10 derniers runs
```

---

## 6. Enrichissement

### 6.1 Ordre des fournisseurs

L'orchestrateur exécute les fournisseurs **dans l'ordre du tableau ci-dessous, tous, à chaque exécution**. Chacun ouvre son `enrichment_runs`, et l'échec de l'un n'empêche pas les suivants.

La règle d'arrêt anticipé — retenir la première valeur obtenue avec une confiance suffisante — s'applique **au sein d'une même exécution**, pour départager deux fournisseurs qui renseignent le même champ. Elle ne dispense jamais d'exécuter un fournisseur : une donnée déjà présente en base n'est pas un verrou. Sauter `contact_extract` parce qu'un contact existe déjà rendrait toute ré-extraction impossible dès qu'un contact obsolète ou saisi à la main est présent. La redondance est traitée par l'unicité `uq_contact` (le contact le plus fiable l'emporte) et par le TTL des faits.

| Rang | Fournisseur | Fournit | Confiance |
|---|---|---|---|
| 1 | `societeinfo` | site, email, téléphone, dirigeant, effectif | 0.90 |
| 2 | `website_probe` | qualité technique du site | 0.95 |
| 3 | `contact_extract` | email/téléphone depuis le site | 0.60 |

**Societeinfo est la source primaire, pas un complément.** Le service est déjà payé et fournit des données contractuellement exploitables. Reconstruire un collecteur de contacts maison serait à la fois moins fiable et juridiquement plus exposé.

### 6.2 Sonde de site (`website_probe`)

Sur l'URL découverte : suivre jusqu'à 3 redirections, timeout 10 s, en-tête `User-Agent` identifiant IOSYS avec une URL de contact.

Signaux collectés :

| Signal | Méthode |
|---|---|
| `website_status_code` | code HTTP final |
| `website_https` | schéma final `https` et certificat valide |
| `website_ttfb_ms` | temps jusqu'au premier octet |
| `website_responsive` | présence de `<meta name="viewport">` |
| `website_cms` | `<meta name="generator">`, puis empreintes de chemins (`/wp-content/`, `/_next/`, `/media/jui/`) |
| `website_copyright_year` | plus grande année trouvée dans le pied de page |

### 6.3 Score de qualité du site

`website_quality_score` sur 100, calculé localement, sans service externe :

| Critère | Points |
|---|---|
| HTTPS valide | 20 |
| Balise viewport présente | 20 |
| TTFB < 800 ms | 15 |
| Copyright de moins de 3 ans | 15 |
| Code 200 | 15 |
| CMS non obsolète (ni Joomla 3, ni WordPress sans viewport) | 15 |

Un score inférieur à 50 est le signal commercial le plus intéressant : le prospect a un site, il investit donc déjà, mais il est mauvais.

### 6.4 Extraction de contacts

Exploration limitée : page d'accueil, plus au maximum les pages `/contact`, `/mentions-legales`, `/nous-contacter` si elles sont liées depuis l'accueil. **Profondeur 1, 4 pages maximum, 1 requête par seconde et par domaine, `robots.txt` respecté.**

Emails : normalisation en minuscules, rejet des adresses jetables et des motifs `noreply@`, `webmaster@`, `postmaster@`. Marquage `is_generic` pour `contact@`, `info@`, `commercial@`.

Téléphones : normalisation E.164 via `phonenumbers`, région par défaut `FR`, rejet des numéros surtaxés (08xx).

### 6.5 Interdits explicites

L'agent **ne doit pas** implémenter, et doit refuser si on le lui demande :

- extraction depuis LinkedIn, Facebook, Instagram (violation des conditions d'utilisation, blocage en pratique sous quelques heures) ;
- extraction depuis Google Maps par scraping (l'accès légitime passe par la Places API, payante et restrictive sur le stockage) ;
- devinette de nom de domaine à partir de la raison sociale (taux de faux positifs inacceptable, et sollicitations envoyées à des tiers sans rapport) ;
- devinette de format d'adresse email (`prenom.nom@domaine`) sans vérification.

### 6.6 Rafraîchissement

Chaque fait porte `expires_at = collected_at + ENRICHMENT_TTL_DAYS`. Le job de mise à jour traite en priorité : (1) les entreprises sans aucun fait, (2) les faits expirés des prospects actifs, (3) les faits expirés du reste.

---

## 7. Moteur de scoring

### 7.1 Principe

Le score est recalculé par un job nocturne et à chaque fin d'enrichissement. Il est **piloté par des données**, pas par du code : modifier une pondération ne doit jamais demander un déploiement.

### 7.2 Prédicats disponibles

| `predicate` | `params` | Vrai si |
|---|---|---|
| `fact_missing` | `{"field": "website_url"}` | aucun fait valide pour ce champ |
| `fact_equals` | `{"field": "website_https", "value": false}` | égalité |
| `fact_lt` | `{"field": "website_quality_score", "value": 50}` | strictement inférieur |
| `fact_gt` | `{"field": "website_ttfb_ms", "value": 1500}` | strictement supérieur |
| `age_days_lt` | `{"days": 30}` | `date_creation` récente |
| `naf_prefix_in` | `{"prefixes": ["41", "43", "68", "69"]}` | code NAF préfixé |
| `departement_in` | `{"codes": ["68", "67"]}` | département |
| `has_contact` | `{"channel": "email"}` | au moins un contact du canal |
| `legal_form_in` | `{"codes": ["5710", "5499"]}` | catégorie juridique |

### 7.3 Jeu de règles initial

| key | predicate | params | points |
|---|---|---|---|
| `no_website` | `fact_equals` | `{"field": "has_website", "value": false}` | **+30** |
| `never_enriched` | `fact_missing` | `{"field": "has_website"}` | +5 |
| `weak_website` | `fact_lt` | `{"field": "website_quality_score", "value": 50}` | **+25** |
| `no_https` | `fact_equals` | `{"field": "website_https", "value": false}` | +10 |
| `not_responsive` | `fact_equals` | `{"field": "website_responsive", "value": false}` | +10 |
| `very_recent` | `age_days_lt` | `{"days": 30}` | +10 |
| `has_email` | `has_contact` | `{"channel": "email"}` | +15 |
| `has_phone` | `has_contact` | `{"channel": "phone"}` | +10 |
| `target_sector` | `naf_prefix_in` | `{"prefixes": ["41","43","68","69","70","71","96"]}` | +10 |

Deux règles restent en base mais **inactives** (`is_active = false`), mises en sommeil par la migration 0008 :

| key | predicate | params | points | réactivable quand |
|---|---|---|---|---|
| `recent` | `age_days_lt` | `{"days": 90}` | +10 | `SIRENE_LOOKBACK_DAYS` dépasse 30 |
| `local` | `departement_in` | `{"codes": ["68","67","90","88"]}` | +10 | `SIRENE_DEPARTEMENTS` s'élargit |

Score final borné à `[0, 100]`. `breakdown` conserve le détail des règles déclenchées, ce qui rend le score explicable dans l'interface — indispensable pour lui faire confiance.

**`no_website` et `never_enriched` sont deux états opposés, pas deux variantes du même.** Une absence de site **constatée** (`has_website = false`) est le signal commercial le plus fort du portefeuille : +30. Une fiche **jamais analysée** ne dit rien de l'entreprise, seulement de notre file de traitement : +5, de quoi la rendre visible sans la placer devant un prospect qualifié. Les deux règles s'excluent mutuellement, `has_website` étant soit présent soit absent.

Un fait `has_website` **expiré** est une absence de connaissance, pas une absence de site : la vue `company_facts` l'écarte, et c'est donc `never_enriched` qui se déclenche à nouveau.

Version initiale de `no_website` : `fact_missing` sur `website_url`, remplacée par la migration 0007. Elle confondait les deux états et faisait remonter en tête de classement les fiches jamais regardées.

**Une règle qui se déclenche sur la quasi-totalité du portefeuille doit être désactivée, pas repondérée.** Elle n'apporte aucune information : elle décale la moyenne sans jamais départager deux fiches, et donne l'illusion d'un score élevé là où il n'y a qu'une constante. C'est le cas de toute règle qui reprend un critère déjà appliqué à la collecte : `local` doublonnait `SIRENE_DEPARTEMENTS`, `recent` et `very_recent` doublonnaient ensemble `SIRENE_LOOKBACK_DAYS`. Constat mesuré sur les 635 premières lignes collectées : 561 d'entre elles partageaient la même tranche de score, pour quatre valeurs distinctes au total. Le contrôle à faire après toute modification du jeu de règles :

```sql
SELECT count(DISTINCT score) FROM (
  SELECT DISTINCT ON (company_id) company_id, score
  FROM score_snapshots ORDER BY company_id, computed_at DESC) s;
```

**Remarque de conception :** ces pondérations sont une hypothèse de départ, pas une vérité. Le croisement `score_snapshots` × `pipeline_events` prévu en 3.7 permettra de les réviser sur des résultats réels après quelques mois.

---
