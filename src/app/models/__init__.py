"""ORM model của SQLAlchemy.

Import mọi model ở đây để `Base.metadata` đầy đủ trước khi Alembic autogenerate
sinh migration.
"""

from app.models.candle import Candle
from app.models.indicator import IndicatorSnapshot, StructureEvent
from app.models.ingestion import IngestionRun
from app.models.symbol import Symbol

__all__ = ["Candle", "IndicatorSnapshot", "IngestionRun", "StructureEvent", "Symbol"]
