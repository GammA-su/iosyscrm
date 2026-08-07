"""Reprises et exécutions du collecteur — section 3.8."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Identity,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import run_status


class CollectorWatermark(Base):
    """Point de reprise de la collecte, écrit dans la transaction du dernier lot."""

    __tablename__ = "collector_watermarks"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CollectorRun(Base):
    """Exécution du collecteur, avec ses compteurs et sa fenêtre temporelle."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(run_status, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column()
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    window_start: Mapped[datetime | None] = mapped_column()
    window_end: Mapped[datetime | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
