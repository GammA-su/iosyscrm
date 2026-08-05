"""État commercial — section 3.3.

Un prospect est créé paresseusement, à la première action commerciale : le
référentiel `companies` reste purgeable sans jamais toucher au travail commercial.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.email import EmailMessage
    from app.models.pipeline import PipelineEvent, PipelineStage
    from app.models.task import Task
    from app.models.user import User


class Prospect(Base, TimestampMixin):
    """Fiche commerciale attachée à une entreprise du référentiel."""

    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    stage_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("pipeline_stages.id"), nullable=False
    )
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("3"))
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"))
    next_action_at: Mapped[datetime | None] = mapped_column()
    estimated_value_cents: Mapped[int | None] = mapped_column(BigInteger)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    entered_stage_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="prospect", lazy="raise")
    stage: Mapped["PipelineStage"] = relationship(back_populates="prospects", lazy="raise")
    owner: Mapped["User | None"] = relationship(back_populates="prospects", lazy="raise")
    events: Mapped[list["PipelineEvent"]] = relationship(
        back_populates="prospect", lazy="raise", passive_deletes=True
    )
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="prospect", lazy="raise", passive_deletes=True
    )
    email_messages: Mapped[list["EmailMessage"]] = relationship(
        back_populates="prospect", lazy="raise", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="prospects_priority_check"),
        Index("idx_prospects_stage", "stage_id"),
        Index("idx_prospects_owner", "owner_id"),
        Index(
            "idx_prospects_next_action",
            "next_action_at",
            postgresql_where=text("next_action_at IS NOT NULL"),
        ),
    )
