"""Faits d'enrichissement et exécutions — section 3.4.

Table de faits générique plutôt que colonnes larges : chaque information porte
sa source, sa date et sa confiance.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import run_status

if TYPE_CHECKING:
    from app.models.company import Company


class EnrichmentFact(Base):
    """Une information enrichie, datée, sourcée et pondérée."""

    __tablename__ = "enrichment_facts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[Any | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    company: Mapped["Company"] = relationship(back_populates="facts", lazy="raise")

    __table_args__ = (
        UniqueConstraint("company_id", "field", "source", name="uq_fact"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="enrichment_facts_confidence_check"),
        Index("idx_facts_company", "company_id"),
        Index("idx_facts_field", "field", "value"),
        Index("idx_facts_expiration", "expires_at"),
    )


class EnrichmentRun(Base):
    """Exécution d'un fournisseur d'enrichissement pour une entreprise."""

    __tablename__ = "enrichment_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(run_status, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column()
    facts_written: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text)

    company: Mapped["Company"] = relationship(back_populates="enrichment_runs", lazy="raise")

    __table_args__ = (Index("idx_enrichment_runs_company", "company_id", text("started_at DESC")),)
