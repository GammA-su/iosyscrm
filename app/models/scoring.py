"""Règles et historique de scoring — section 3.7.

Les scores sont historisés plutôt qu'écrasés : croiser `score_snapshots` avec
`pipeline_events` permettra de recalibrer les pondérations sur des données réelles.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company


class ScoringRule(Base):
    """Règle de scoring pilotée par les données, pas par le code."""

    __tablename__ = "scoring_rules"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(String(24), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    points: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScoreSnapshot(Base):
    """Score calculé à un instant donné, avec son détail explicable."""

    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ruleset_hash: Mapped[str] = mapped_column(CHAR(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="score_snapshots", lazy="raise")

    __table_args__ = (
        Index("idx_scores_latest", "company_id", text("computed_at DESC")),
        Index("idx_scores_ranking", text("score DESC"), text("computed_at DESC")),
    )
