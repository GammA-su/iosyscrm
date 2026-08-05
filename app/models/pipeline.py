"""Étapes du pipeline et historique des transitions — section 3.3."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.prospect import Prospect
    from app.models.user import User


class PipelineStage(Base):
    """Colonne du kanban. Les identifiants sont fixés par le seed de migration."""

    __tablename__ = "pipeline_stages"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    color: Mapped[str] = mapped_column(
        String(7), nullable=False, server_default=text("'#6c757d'::character varying")
    )
    is_won: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_lost: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    prospects: Mapped[list["Prospect"]] = relationship(back_populates="stage", lazy="raise")


class PipelineEvent(Base):
    """Trace immuable d'un changement d'étape."""

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    prospect_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False
    )
    from_stage_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("pipeline_stages.id")
    )
    to_stage_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("pipeline_stages.id"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    prospect: Mapped["Prospect"] = relationship(back_populates="events", lazy="raise")
    from_stage: Mapped["PipelineStage | None"] = relationship(
        foreign_keys=[from_stage_id], lazy="raise"
    )
    to_stage: Mapped["PipelineStage"] = relationship(foreign_keys=[to_stage_id], lazy="raise")
    user: Mapped["User | None"] = relationship(lazy="raise")

    __table_args__ = (
        Index("idx_pipeline_events_prospect", "prospect_id", text("created_at DESC")),
    )
