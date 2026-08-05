"""Modèles SQLAlchemy.

Ce module importe tous les modèles afin qu'Alembic voie l'intégralité de
`Base.metadata` en n'important que `app.models`.
"""

from app.database.base import Base
from app.models.collector import CollectorRun, CollectorWatermark
from app.models.company import Company
from app.models.contact import Contact, OptOut
from app.models.email import EmailMessage, EmailTemplate
from app.models.enrichment import EnrichmentFact, EnrichmentRun
from app.models.pipeline import PipelineEvent, PipelineStage
from app.models.prospect import Prospect
from app.models.scoring import ScoreSnapshot, ScoringRule
from app.models.task import Task
from app.models.user import User, UserSession

__all__ = [
    "Base",
    "CollectorRun",
    "CollectorWatermark",
    "Company",
    "Contact",
    "EmailMessage",
    "EmailTemplate",
    "EnrichmentFact",
    "EnrichmentRun",
    "OptOut",
    "PipelineEvent",
    "PipelineStage",
    "Prospect",
    "ScoreSnapshot",
    "ScoringRule",
    "Task",
    "User",
    "UserSession",
]
