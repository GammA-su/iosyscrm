"""Configuration : validateurs de la section 4."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_production_rejects_short_secret_key() -> None:
    with pytest.raises(PydanticValidationError, match="APP_SECRET_KEY"):
        _settings(APP_ENV="production", APP_SECRET_KEY="trop-court")


def test_production_accepts_long_secret_key() -> None:
    settings = _settings(APP_ENV="production", APP_SECRET_KEY="s" * 32)

    assert settings.is_production is True


def test_development_tolerates_empty_secret_key() -> None:
    settings = _settings(APP_ENV="development", APP_SECRET_KEY="")

    assert settings.is_production is False


def test_csv_lists_are_split() -> None:
    settings = _settings(SIRENE_DEPARTEMENTS="68, 67 ,90", SIRENE_NAF_EXCLUDE="84,85")

    assert settings.SIRENE_DEPARTEMENTS == ["68", "67", "90"]
    assert settings.SIRENE_NAF_EXCLUDE == ["84", "85"]


def test_csv_lists_default_to_specification_values() -> None:
    settings = _settings()

    assert settings.SIRENE_DEPARTEMENTS == ["68", "67", "90", "88"]
    assert settings.SIRENE_NAF_EXCLUDE == ["84", "85", "86", "87", "88"]


def test_empty_csv_list_is_empty() -> None:
    settings = _settings(SIRENE_DEPARTEMENTS="")

    assert settings.SIRENE_DEPARTEMENTS == []
