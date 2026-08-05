"""Gabarits et messages email — section 3.6.

Le corps final rendu est stocké : un gabarit modifié plus tard ne doit pas
réécrire l'historique de ce qui a réellement été envoyé.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import email_status

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.prospect import Prospect
    from app.models.user import User


class EmailTemplate(Base, TimestampMixin):
    """Gabarit Jinja2 réutilisable."""

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class EmailMessage(Base):
    """Message destiné à un prospect, dans son état final rendu."""

    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    prospect_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("contacts.id", ondelete="SET NULL")
    )
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("email_templates.id", ondelete="SET NULL")
    )
    to_address: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        email_status, nullable=False, server_default=text("'draft'::email_status")
    )
    unsubscribe_token: Mapped[str] = mapped_column(CHAR(43), nullable=False, unique=True)
    scheduled_at: Mapped[datetime | None] = mapped_column()
    sent_at: Mapped[datetime | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    prospect: Mapped["Prospect"] = relationship(back_populates="email_messages", lazy="raise")
    contact: Mapped["Contact | None"] = relationship(lazy="raise")
    template: Mapped["EmailTemplate | None"] = relationship(lazy="raise")
    author: Mapped["User | None"] = relationship(lazy="raise")

    __table_args__ = (
        Index("idx_email_queue", "scheduled_at", postgresql_where=text("status = 'queued'")),
        Index("idx_email_prospect", "prospect_id", text("created_at DESC")),
    )
