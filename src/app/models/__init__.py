"""ORM model của SQLAlchemy.

Import mọi model ở đây để `Base.metadata` đầy đủ trước khi Alembic autogenerate
sinh migration.
"""

from app.models.candle import Candle
from app.models.indicator import IndicatorSnapshot, StructureEvent
from app.models.ingestion import IngestionRun
from app.models.symbol import Symbol

__all__ = ["Candle", "IndicatorSnapshot", "IngestionRun", "StructureEvent", "Symbol"]

from app.models.analysis_case import AnalysisCase, CaseEmbedding  # noqa: F401
from app.models.analysis_evidence import (  # noqa: F401
    AnalysisClaim,
    AnalysisEnrichment,
    AnalysisEvidence,
    NewsDocument,
    ProviderCache,
)
from app.models.rag_document import RagDocument  # noqa: F401
