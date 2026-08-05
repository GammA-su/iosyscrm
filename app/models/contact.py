"""Contacts et liste d'opposition — section 3.5."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import contact_channel, contact_origin

if TYPE_CHECKING:
    from app.models.company import Company


class Contact(Base):
    """Point de contact rattaché à une entreprise."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(contact_channel, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    display_value: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(contact_origin, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    is_generic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    verified_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="contacts", lazy="raise")

    __table_args__ = (
        UniqueConstraint("company_id", "channel", "value", name="uq_contact"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="contacts_confidence_check"),
        Index(
            "idx_contact_primary",
            "company_id",
            "channel",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class OptOut(Base):
    """Opposition globale : elle vaut pour l'adresse, jamais pour une fiche.

    Aucune clé étrangère, volontairement : si l'entreprise est purgée,
    l'opposition doit survivre.
    """

    __tablename__ = "opt_outs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel: Mapped[str] = mapped_column(contact_channel, nullable=False)
    value_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("channel", "value_hash", name="uq_optout"),)
