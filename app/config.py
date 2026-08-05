"""Configuration applicative.

Toute variable d'environnement du projet est déclarée ici, avec son type et sa
valeur par défaut. Aucun `os.getenv` ailleurs dans le code (section 4).
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY_LENGTH = 32


def _split_csv(value: Any) -> Any:
    """Découpe une chaîne « a,b,c » en liste, laisse passer le reste tel quel."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """Variables de configuration, section 4 du cahier des charges."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    APP_ENV: Literal["development", "production"] = "development"
    APP_SECRET_KEY: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    APP_LOG_LEVEL: str = "INFO"

    # --- Base de données ---
    DATABASE_URL: str = "postgresql+psycopg://crm:crm@localhost:5432/prospectcrm"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_ECHO: bool = False

    # --- API SIRENE (INSEE) ---
    SIRENE_API_KEY: str = ""
    SIRENE_BASE_URL: str = "https://api.insee.fr/api-sirene/3.11"
    SIRENE_RATE_LIMIT_PER_MINUTE: int = 28
    SIRENE_PAGE_SIZE: int = 1000
    SIRENE_LOOKBACK_DAYS: int = 30
    SIRENE_DEPARTEMENTS: list[str] = ["68", "67", "90", "88"]
    SIRENE_NAF_EXCLUDE: list[str] = ["84", "85", "86", "87", "88"]

    # --- Enrichissement ---
    ENRICHMENT_ENABLED: bool = True
    ENRICHMENT_BATCH_SIZE: int = 50
    ENRICHMENT_MAX_WORKERS: int = 8
    ENRICHMENT_USER_AGENT: str = "IOSYS-ProspectBot/1.0 (+https://iosys.fr/bot)"
    ENRICHMENT_TIMEOUT_SECONDS: int = 10
    ENRICHMENT_TTL_DAYS: int = 90
    ENRICHMENT_RESPECT_ROBOTS: bool = True

    SOCIETEINFO_ENABLED: bool = True
    SOCIETEINFO_API_KEY: str = ""
    SOCIETEINFO_BASE_URL: str = ""

    # --- Email ---
    MAIL_ENABLED: bool = False
    MAIL_SMTP_HOST: str = ""
    MAIL_SMTP_PORT: int = 587
    MAIL_SMTP_USER: str = ""
    MAIL_SMTP_PASSWORD: str = ""
    MAIL_FROM_ADDRESS: str = ""
    MAIL_FROM_NAME: str = "IOSYS"
    MAIL_REPLY_TO: str = ""
    MAIL_DAILY_LIMIT: int = 80
    MAIL_MIN_INTERVAL_SECONDS: int = 45

    # --- Conformité ---
    COMPLIANCE_RETENTION_DAYS: int = 1095
    COMPLIANCE_CONTROLLER_NAME: str = "IOSYS SAS"
    COMPLIANCE_CONTROLLER_ADDRESS: str = ""
    COMPLIANCE_CONTACT_EMAIL: str = ""

    # --- Ordonnanceur ---
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_TIMEZONE: str = "Europe/Paris"

    @field_validator("SIRENE_DEPARTEMENTS", "SIRENE_NAF_EXCLUDE", mode="before")
    @classmethod
    def _parse_csv_list(cls, value: Any) -> Any:
        return _split_csv(value)

    @model_validator(mode="after")
    def _check_production_secret(self) -> "Settings":
        if self.APP_ENV == "production" and len(self.APP_SECRET_KEY) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                "APP_SECRET_KEY doit faire au moins "
                f"{MIN_SECRET_KEY_LENGTH} caractères en production"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instance unique de configuration, mise en cache pour tout le processus."""
    return Settings()
