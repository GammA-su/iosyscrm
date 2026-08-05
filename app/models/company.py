"""Référentiel SIRENE — section 3.2.

`companies` est un miroir fidèle de SIRENE : aucune donnée commerciale, donc
resynchronisable intégralement sans risque.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.enrichment import EnrichmentFact, EnrichmentRun
    from app.models.prospect import Prospect
    from app.models.scoring import ScoreSnapshot


class Company(Base):
    """Unité légale SIRENE, sur l'établissement siège uniquement."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    siren: Mapped[str] = mapped_column(CHAR(9), nullable=False, unique=True)
    siret_siege: Mapped[str] = mapped_column(CHAR(14), nullable=False)
    denomination: Mapped[str | None] = mapped_column(Text)
    nom_complet: Mapped[str | None] = mapped_column(Text)
    is_personne_physique: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    categorie_juridique: Mapped[str | None] = mapped_column(String(4))
    activite_principale: Mapped[str | None] = mapped_column(String(6))
    tranche_effectifs: Mapped[str | None] = mapped_column(String(2))
    date_creation: Mapped[date] = mapped_column(Date, nullable=False)
    etat_administratif: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    statut_diffusion: Mapped[str] = mapped_column(
        CHAR(1), nullable=False, server_default=text("'O'::bpchar")
    )
    adresse_numero: Mapped[str | None] = mapped_column(String(10))
    adresse_type_voie: Mapped[str | None] = mapped_column(String(20))
    adresse_libelle_voie: Mapped[str | None] = mapped_column(Text)
    adresse_complement: Mapped[str | None] = mapped_column(Text)
    code_postal: Mapped[str | None] = mapped_column(String(5))
    commune: Mapped[str | None] = mapped_column(Text)
    code_commune: Mapped[str | None] = mapped_column(String(5))
    departement: Mapped[str | None] = mapped_column(String(3))
    date_dernier_traitement: Mapped[datetime | None] = mapped_column()
    first_seen_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_synced_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    prospect: Mapped["Prospect | None"] = relationship(
        back_populates="company", uselist=False, lazy="raise", passive_deletes=True
    )
    facts: Mapped[list["EnrichmentFact"]] = relationship(
        back_populates="company", lazy="raise", passive_deletes=True
    )
    enrichment_runs: Mapped[list["EnrichmentRun"]] = relationship(
        back_populates="company", lazy="raise", passive_deletes=True
    )
    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="company", lazy="raise", passive_deletes=True
    )
    score_snapshots: Mapped[list["ScoreSnapshot"]] = relationship(
        back_populates="company", lazy="raise", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "denomination IS NOT NULL OR nom_complet IS NOT NULL OR statut_diffusion = 'P'",
            name="companies_name_present",
        ),
        Index("idx_companies_date_creation", text("date_creation DESC")),
        Index("idx_companies_departement", "departement"),
        Index("idx_companies_naf", "activite_principale"),
        Index("idx_companies_dernier_traitement", text("date_dernier_traitement DESC")),
        # La classe d'opérateurs passe par `postgresql_ops` et non dans
        # l'expression : sinon Alembic ne sait pas comparer l'index et le
        # signale à chaque autogenerate.
        Index(
            "idx_companies_denomination_trgm",
            text("coalesce(denomination, nom_complet)"),
            postgresql_using="gin",
            postgresql_ops={"coalesce(denomination, nom_complet)": "gin_trgm_ops"},
        ),
        Index(
            "idx_companies_actives",
            text("date_creation DESC"),
            postgresql_where=text("etat_administratif = 'A'"),
        ),
    )
