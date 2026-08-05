"""Authentification par session serveur — section 9.

Pas de JWT : le produit est un monolithe à session, et la révocation immédiate
est un besoin réel. Le jeton est aléatoire, transmis en cookie, et seul son
SHA-256 est stocké : une fuite de la table `sessions` ne permet pas de rejouer
une session.
"""

import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from sqlalchemy.orm import Session

from app.exceptions import AuthenticationError, RateLimitedError
from app.models.user import User, UserSession
from app.repositories import session as session_repo
from app.repositories import user as user_repo

#: Nom du cookie de session (section 9).
SESSION_COOKIE_NAME = "session"

#: Durée d'une session, glissante.
SESSION_TTL = timedelta(days=7)

#: Seuil de prolongation : en dessous, `expires_at` repart à +7 jours.
SESSION_RENEW_BELOW = timedelta(days=6)

#: Limitation des tentatives de connexion (section 9).
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = timedelta(minutes=15)

#: Argon2id, paramètres par défaut de la bibliothèque (section 9).
_password_hasher = PasswordHasher()

# Compteur de tentatives échouées, PAR PROCESSUS et EN MÉMOIRE.
#
# Limite assumée : le compteur n'est pas partagé entre processus et disparaît au
# redémarrage. Le déploiement cible est un conteneur `app` unique (section 14),
# donc la protection est effective. Si l'API venait à tourner avec plusieurs
# workers Uvicorn ou plusieurs répliques, la limite deviendrait de
# 5 fois N tentatives et il faudrait la déplacer en base ou dans un cache partagé.
_failed_attempts: dict[str, list[datetime]] = {}
_failed_attempts_lock = threading.Lock()


def hash_password(password: str) -> str:
    """Empreinte Argon2id d'un mot de passe en clair."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Vérifie un mot de passe contre son empreinte, sans jamais lever."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    """SHA-256 hexadécimal du jeton de session : c'est la seule forme stockée."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    """Forme canonique d'une adresse : sans espaces et en minuscules."""
    return email.strip().lower()


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _normalize_ip(value: str | None) -> str | None:
    """Garde l'adresse si c'est une IP valide, sinon `None`.

    `sessions.ip` est de type INET : une valeur non conforme ferait échouer
    l'insertion, donc la connexion entière. Un client sans adresse routable
    (proxy mal configuré, client de test) ne doit pas empêcher de se connecter.
    """
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def is_rate_limited(email: str) -> bool:
    """Indique si l'adresse a épuisé son quota de tentatives sur la fenêtre."""
    key = normalize_email(email)
    horizon = _now() - LOGIN_WINDOW
    with _failed_attempts_lock:
        attempts = [moment for moment in _failed_attempts.get(key, []) if moment > horizon]
        if attempts:
            _failed_attempts[key] = attempts
        else:
            _failed_attempts.pop(key, None)
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_failed_attempt(email: str) -> None:
    """Enregistre une tentative échouée pour cette adresse."""
    key = normalize_email(email)
    horizon = _now() - LOGIN_WINDOW
    with _failed_attempts_lock:
        attempts = [moment for moment in _failed_attempts.get(key, []) if moment > horizon]
        attempts.append(_now())
        _failed_attempts[key] = attempts


def reset_failed_attempts(email: str) -> None:
    """Efface le compteur d'une adresse, après une connexion réussie."""
    with _failed_attempts_lock:
        _failed_attempts.pop(normalize_email(email), None)


def clear_rate_limiter() -> None:
    """Vide entièrement le compteur. Réservé aux tests."""
    with _failed_attempts_lock:
        _failed_attempts.clear()


def authenticate(db: Session, email: str, password: str) -> User:
    """Valide un couple email / mot de passe.

    Lève `RateLimitedError` au-delà de `LOGIN_MAX_ATTEMPTS` échecs sur la
    fenêtre, et `AuthenticationError` si les identifiants sont faux ou le
    compte désactivé. Le message est identique dans les deux derniers cas :
    l'existence d'un compte ne doit pas être déductible de la réponse.
    """
    key = normalize_email(email)
    if is_rate_limited(key):
        raise RateLimitedError("Trop de tentatives de connexion. Réessayez dans quelques minutes.")

    user = user_repo.get_by_email(db, key)
    password_ok = user is not None and verify_password(user.password_hash, password)
    if user is None or not user.is_active or not password_ok:
        record_failed_attempt(key)
        raise AuthenticationError("Adresse email ou mot de passe invalide.")

    reset_failed_attempts(key)
    return user


def create_session(
    db: Session,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Ouvre une session et renvoie le jeton EN CLAIR, seule occasion de le lire.

    Le jeton fait 32 octets aléatoires (`secrets.token_urlsafe`) ; la base ne
    reçoit que son SHA-256.
    """
    token = secrets.token_urlsafe(32)
    session_repo.add(
        db,
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=_now() + SESSION_TTL,
            ip=_normalize_ip(ip),
            user_agent=user_agent,
        ),
    )
    db.commit()
    return token


def resolve_session(db: Session, token: str) -> User | None:
    """Utilisateur associé à un jeton, ou `None`.

    Refuse une session expirée comme un compte désactivé. Prolonge la session
    à +7 jours dès qu'il reste moins de `SESSION_RENEW_BELOW`, ce qui borne
    l'écriture à une fois par jour et par session.
    """
    stored = session_repo.get_by_token_hash(db, hash_token(token))
    if stored is None:
        return None

    now = _now()
    if stored.expires_at <= now:
        return None

    user = stored.user
    if not user.is_active:
        return None

    if stored.expires_at - now < SESSION_RENEW_BELOW:
        stored.expires_at = now + SESSION_TTL
        db.commit()

    return user


def revoke_session(db: Session, token: str) -> bool:
    """Supprime la session portant ce jeton. Renvoie `True` si une ligne a sauté."""
    removed = session_repo.delete_by_token_hash(db, hash_token(token))
    db.commit()
    return removed > 0


def revoke_all_sessions_for_user(db: Session, user: User) -> int:
    """Supprime toutes les sessions d'un utilisateur. Renvoie leur nombre."""
    removed = session_repo.delete_for_user(db, user.id)
    db.commit()
    return removed
