"""ORM model của SQLAlchemy.

Import mọi model ở đây để `Base.metadata` đầy đủ trước khi Alembic autogenerate
sinh migration.
"""

from app.models.analysis_case import AnalysisCase, CaseEmbedding
from app.models.analysis_evidence import (
    AnalysisClaim,
    AnalysisEnrichment,
    AnalysisEvidence,
    NewsDocument,
    ProviderCache,
)
from app.models.candle import Candle
from app.models.chat import ChatTurn, Conversation
from app.models.indicator import IndicatorSnapshot, StructureEvent
from app.models.ingestion import IngestionRun
from app.models.rag_document import RagDocument
from app.models.symbol import Symbol
from app.models.watchlist import Watchlist, WatchlistItem

__all__ = [
    "AnalysisCase",
    "AnalysisClaim",
    "AnalysisEnrichment",
    "AnalysisEvidence",
    "Candle",
    "CaseEmbedding",
    "ChatTurn",
    "Conversation",
    "IndicatorSnapshot",
    "IngestionRun",
    "NewsDocument",
    "ProviderCache",
    "RagDocument",
    "StructureEvent",
    "Symbol",
    "Watchlist",
    "WatchlistItem",
]
