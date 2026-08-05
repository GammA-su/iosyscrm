"""Types ENUM PostgreSQL de la section 3.1.

Chaque type est déclaré **une seule fois** ici et référencé par les colonnes
qui l'utilisent. `create_type=False` est indispensable : sinon SQLAlchemy émet
un `CREATE TYPE` à chaque `CREATE TABLE` référençant le type, et la deuxième
table échoue. La création effective des types est faite explicitement en tête
de la migration 0001.
"""

from typing import Final

from sqlalchemy.dialects import postgresql

CONTACT_CHANNEL_VALUES: Final[tuple[str, ...]] = ("email", "phone")
CONTACT_ORIGIN_VALUES: Final[tuple[str, ...]] = ("societeinfo", "website", "manual", "sirene")
EMAIL_STATUS_VALUES: Final[tuple[str, ...]] = (
    "draft",
    "queued",
    "sent",
    "failed",
    "bounced",
    "cancelled",
)
RUN_STATUS_VALUES: Final[tuple[str, ...]] = ("running", "success", "partial", "failed")
USER_ROLE_VALUES: Final[tuple[str, ...]] = ("admin", "commercial", "viewer")

contact_channel = postgresql.ENUM(
    *CONTACT_CHANNEL_VALUES, name="contact_channel", create_type=False
)
contact_origin = postgresql.ENUM(*CONTACT_ORIGIN_VALUES, name="contact_origin", create_type=False)
email_status = postgresql.ENUM(*EMAIL_STATUS_VALUES, name="email_status", create_type=False)
run_status = postgresql.ENUM(*RUN_STATUS_VALUES, name="run_status", create_type=False)
user_role = postgresql.ENUM(*USER_ROLE_VALUES, name="user_role", create_type=False)

#: Ordre de création des types, repris tel quel par la migration 0001.
ENUM_TYPES: Final[tuple[postgresql.ENUM, ...]] = (
    contact_channel,
    contact_origin,
    email_status,
    run_status,
    user_role,
)
