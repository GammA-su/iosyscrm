# RGPD, authentification

Fragment de CAHIER-DES-CHARGES.md — Prospect CRM IOSYS.
Numérotation des sections conservée : les renvois du type « section 5.2 » restent valides.

---

## 8. Conformité RGPD

Section non optionnelle. Le fichier contient des données personnelles dès qu'il inclut des entrepreneurs individuels ou des emails nominatifs.

### 8.1 Obligations à implémenter

| Obligation | Implémentation |
|---|---|
| Base légale | Intérêt légitime (prospection B2B), documentée dans `README.md` |
| Information des personnes | Bloc obligatoire en pied de chaque email généré (voir 8.2) |
| Droit d'opposition | Route publique `GET/POST /desinscription/{token}`, sans authentification |
| Effectivité de l'opposition | Vérification `opt_outs` **au moment de l'envoi**, pas à la création |
| Limitation de conservation | Job hebdomadaire de purge à `COMPLIANCE_RETENTION_DAYS` après le dernier contact |
| Traçabilité | `pipeline_events` et `email_messages` conservent l'historique complet |
| Minimisation | Vocabulaire fermé de `field` (6.4), aucune donnée sensible collectée |
| Sécurité | Argon2id, cookies `HttpOnly` + `Secure` + `SameSite=Lax`, TLS obligatoire |

### 8.2 Pied de page obligatoire

Ajouté automatiquement par le service `mailer`, **non désactivable et non modifiable depuis l'interface** :

```
---
{{ controller_name }} — {{ controller_address }}
Vous recevez ce message dans le cadre d'une prospection commerciale
professionnelle. Vos coordonnées professionnelles proviennent de sources
publiques (base SIRENE de l'INSEE) et de votre site internet.
Vous disposez d'un droit d'accès, de rectification et d'opposition :
{{ base_url }}/desinscription/{{ token }} ou {{ contact_email }}.
```

### 8.3 Garde-fous d'envoi

Avant tout envoi, le `mailer` vérifie dans l'ordre, et abandonne au premier échec :

1. `MAIL_ENABLED` est vrai ;
2. l'adresse n'est pas dans `opt_outs` ;
3. le quota `MAIL_DAILY_LIMIT` n'est pas atteint ;
4. `MAIL_MIN_INTERVAL_SECONDS` est écoulé depuis le dernier envoi ;
5. aucun email n'a déjà été envoyé à cette adresse dans les 14 derniers jours.

### 8.4 Purge

Job hebdomadaire : suppression des `companies` sans prospect associé, sans contact, et vues pour la dernière fois il y a plus de `COMPLIANCE_RETENTION_DAYS`. Les entrées `opt_outs` ne sont **jamais** purgées.

---

## 9. Authentification

Sessions serveur, pas de JWT : l'application est un monolithe à session, la révocation immédiate est un besoin réel, et un JWT n'apporterait ici qu'une complexité gratuite.

- Hachage Argon2id, paramètres par défaut d'`argon2-cffi`.
- Jeton de session : 32 octets aléatoires, transmis en cookie `HttpOnly`, `Secure` (hors développement), `SameSite=Lax`. Seul le SHA-256 est stocké.
- Durée : 7 jours glissants, prolongée à chaque requête si plus de 24 h restantes.
- Limitation : 5 tentatives échouées par adresse email et par tranche de 15 minutes.
- Rôles : `admin` (tout, y compris utilisateurs et règles de scoring), `commercial` (tout sauf administration), `viewer` (lecture seule).
- Création du premier administrateur : `uv run crm users create --email … --admin`. **Aucun compte par défaut dans les migrations.**

---
