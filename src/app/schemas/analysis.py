"""Validated analysis contracts, including an outcome-free evidence schema."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.evidence import IndicatorCondition, Stage


class AnalysisCreate(BaseModel):
    """Point-in-time facts; clients must supply only information known then."""

    model_config = ConfigDict(extra="forbid")
    symbol: str | None = Field(default=None, pattern=r"^[A-Z0-9._-]{1,20}$")
    timeframe: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,20}$")
    analyzed_at: AwareDatetime | None = Field(
        default=None, validation_alias=AliasChoices("analyzed_at", "analysis_at")
    )
    thesis: str = Field(
        min_length=1,
        max_length=50_000,
        validation_alias=AliasChoices("thesis", "notes", "user_notes"),
    )
    indicator_snapshot: dict[str, Any] = Field(default_factory=dict)
    market_context: dict[str, Any] = Field(default_factory=dict)
    conclusion: str = Field(default="", max_length=10_000)
    source: str = Field(default="user", min_length=1, max_length=1000)

    exchange: str | None = Field(default=None, max_length=30)
    price_basis: Literal["RAW", "ADJUSTED"] = "RAW"
    company_name: str = Field(default="", max_length=200)
    company_aliases: list[str] = Field(default_factory=list, max_length=10)
    sector: str = Field(default="", max_length=200)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("indicator_snapshot", "market_context", "custom_metadata")
    @classmethod
    def bound_metadata(cls, value: dict) -> dict:
        """Bound nested metadata serialized size and reject non-JSON numbers."""
        import json

        if len(json.dumps(value, allow_nan=False)) > 50_000:
            raise ValueError("Metadata exceeds 50 KB")
        return value


class CaseEvidence(AnalysisCreate):
    """Safe historical decision evidence: intentionally excludes all future outcomes."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: UUID
    chart_path: str | None
    ingest_status: Stage | Literal["PROCESSING"]
    enrichment: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CaseDetail(CaseEvidence):
    """User detail view may explicitly display outcomes outside agent evidence."""

    outcome: dict[str, Any] | None


class AnalysisSearch(BaseModel):
    """Text query embedded in the same configured space as stored cases."""

    query: str = Field(default="", max_length=10_000)
    image: str | None = Field(default=None, max_length=14_000_000)
    conditions: list[IndicatorCondition] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_query(self):
        """Image-only search is valid; empty search and inverted time windows are not."""
        if not self.query.strip() and not self.image:
            raise ValueError("Provide query text or image")
        if self.before and self.after and self.after >= self.before:
            raise ValueError("after must precede before")
        return self

    symbol: str | None = Field(default=None, pattern=r"^[A-Z0-9._-]{1,20}$")
    timeframe: str | None = Field(default=None, max_length=20)
    top_k: int = Field(default=5, ge=1, le=50)
    before: AwareDatetime | None = None
    after: AwareDatetime | None = None


class CaseMatch(BaseModel):
    """Database cosine similarity and outcome-free evidence."""

    similarity: float
    case: CaseEvidence
    fusion_score: float | None = None
    channel_similarities: dict[str, float] = Field(default_factory=dict)
    rerank_score: float | None = None


class OutcomeUpdate(BaseModel):
    """Independent outcome patch; cannot overwrite the original thesis."""

    model_config = ConfigDict(extra="forbid")
    outcome: dict[str, Any]

    @field_validator("outcome")
    @classmethod
    def bound_outcome(cls, value: dict) -> dict:
        """Apply the same bounded JSON contract used for point-in-time metadata."""
        return AnalysisCreate.bound_metadata(value)


class CaseResponse(BaseModel):
    """Consistent success envelope for one case."""

    data: CaseDetail
    job_id: UUID | None = None


class SearchResponse(BaseModel):
    """Consistent success envelope for ranked case evidence.

    ``verification_summary`` aggregates persisted claim statuses across returned
    cases; it never implies that an investment thesis will succeed.
    """

    data: list[CaseMatch]
    case_ids: list[UUID] = Field(default_factory=list)
    verification_summary: dict[str, int] = Field(default_factory=dict)
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    rerank_metadata: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)


class MultimodalQuery(AnalysisSearch):
    """A file-only query has an explicit default retrieval question."""

    query: str = Field(default="Find similar historical chart analyses", max_length=10_000)
