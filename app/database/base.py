"""Base déclarative SQLAlchemy et mixins communs."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import TIMESTAMP, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base déclarative commune à tous les modèles."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: TIMESTAMP(timezone=True),
        dict[str, Any]: JSONB,
        str: Text,
    }


class TimestampMixin:
    """Colonnes `created_at` / `updated_at` gérées par PostgreSQL."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
