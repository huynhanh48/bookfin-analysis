"""Typed observations, claims and provenance; uncertainty remains explicit."""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Stage(StrEnum):
    """Persisted enrichment stages, including safe terminal partial/review states."""

    PENDING = "PENDING"
    EXTRACTING = "EXTRACTING"
    ENRICHING = "ENRICHING"
    VERIFYING = "VERIFYING"
    NORMALIZING = "NORMALIZING"
    INDEXING = "INDEXING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class VerificationStatus(StrEnum):
    """Verification describes the claim, never whether an investment will succeed."""

    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"


class ClaimTemporalScope(BaseModel):
    """Explicit temporal scope for a claim; unknown is safer than guessing."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["analysis", "divergence", "explicit", "unknown"] = "unknown"
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    basis: Literal["user_note", "divergence_event", "explicit_date", "inferred"] = "inferred"


class TechnicalClaim(BaseModel):
    """One user/vision assertion with traceable deterministic verification evidence."""

    model_config = ConfigDict(extra="forbid")
    claim_id: UUID = Field(default_factory=uuid4)
    claim_type: Literal[
        "rsi_bullish_divergence",
        "rsi_bearish_divergence",
        "macd_bullish_divergence",
        "macd_bearish_divergence",
        "bullish_engulfing",
        "bearish_engulfing",
        "volume_increase",
        "money_inflow",
        "breakout",
        "bearish_breakout",
        "rsi_overbought",
        "rsi_oversold",
        "historical_rsi_overbought",
        "historical_rsi_oversold",
        "rsi_below_30",
        "rsi_above_70",
        "rsi_oversold_in_divergence",
        "rsi_overbought_in_divergence",
        "other",
    ]
    original_text: str = Field(min_length=1, max_length=2000)
    extracted_parameters: dict[str, Any] = Field(default_factory=dict)
    temporal_scope: ClaimTemporalScope | None = None
    source_type: Literal["user", "vision"] = "user"
    verification_status: VerificationStatus = VerificationStatus.INSUFFICIENT_EVIDENCE
    verification_reason: str = "Not verified yet."
    evidence_ids: list[str] = Field(default_factory=list)


class VisionObservations(BaseModel):
    """Only visible observations; provider cannot mark technical claims verified."""

    model_config = ConfigDict(extra="forbid")
    symbol_candidates: list[str] = Field(default_factory=list, max_length=10)
    timeframe: str | None = None
    chart_observed_at: AwareDatetime | None = None
    exchange: str | None = None
    description: str = Field(default="", max_length=10_000)
    visible_indicators: list[str] = Field(default_factory=list, max_length=30)
    price_structure: list[str] = Field(default_factory=list, max_length=30)
    trendlines: list[str] = Field(default_factory=list, max_length=30)
    support_resistance: list[str] = Field(default_factory=list, max_length=30)
    candle_patterns: list[str] = Field(default_factory=list, max_length=30)
    divergence_observations: list[str] = Field(default_factory=list, max_length=30)
    volume_observations: list[str] = Field(default_factory=list, max_length=30)
    annotations: list[str] = Field(default_factory=list, max_length=30)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)
    claims: list[TechnicalClaim] = Field(default_factory=list, max_length=30)


class ExtractionResult(BaseModel):
    """LLM extraction envelope; observations and user claims are kept separate."""

    model_config = ConfigDict(extra="forbid")
    observations: VisionObservations
    user_claims: list[TechnicalClaim] = Field(default_factory=list, max_length=30)


class NewsEvidence(BaseModel):
    """Publication and update timestamps are separate; historical eligibility is strict."""

    source_url: str
    canonical_url: str
    title: str = ""
    publisher: str = ""
    published_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    content: str = Field(max_length=100_000)
    content_hash: str
    relevance: float = Field(ge=0, le=1)
    associated_symbol: str
    associated_sector: str | None = None
    source_type: str = "news"


class IndicatorCondition(BaseModel):
    """Allowlisted deterministic SQL filter, never arbitrary SQL or JSON path."""

    field: Literal["rsi", "adx", "atr", "rvol", "obv", "macd", "macd_histogram"]
    operator: Literal["gte", "lte", "eq"] = "gte"
    value: float = Field(allow_inf_nan=False)


class JobDetail(BaseModel):
    """Safe polling contract; provider exception bodies and secrets are excluded."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    case_id: UUID | None
    status: str
    stage: str
    progress: int
    attempts: int
    error_code: str | None
    diagnostics: dict[str, Any]


class JobResponse(BaseModel):
    """Consistent polling envelope, independent of worker implementation."""

    data: JobDetail
