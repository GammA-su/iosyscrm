"""Tâches commerciales — section 3.6."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.prospect import Prospect
    from app.models.user import User


class Task(Base):
    """Rappel, appel, email ou rendez-vous attaché à un prospect."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    prospect_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'rappel'::character varying")
    )
    due_at: Mapped[datetime] = mapped_column(nullable=False)
    done_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    prospect: Mapped["Prospect"] = relationship(back_populates="tasks", lazy="raise")
    user: Mapped["User | None"] = relationship(lazy="raise")

    __table_args__ = (Index("idx_tasks_due", "due_at", postgresql_where=text("done_at IS NULL")),)
