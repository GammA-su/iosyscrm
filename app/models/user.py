"""Utilisateurs et sessions — section 3.8."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import user_role

if TYPE_CHECKING:
    from app.models.prospect import Prospect


class User(Base):
    """Utilisateur de l'application. Authentification par session, pas par JWT."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        user_role, nullable=False, server_default=text("'commercial'::user_role")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    prospects: Mapped[list["Prospect"]] = relationship(back_populates="owner", lazy="raise")
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )


class UserSession(Base):
    """Session authentifiée. Table `sessions` de la section 3.8."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sessions", lazy="raise")

    __table_args__ = (Index("idx_sessions_expiry", "expires_at"),)
