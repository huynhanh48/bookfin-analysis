"""Public request and result contracts for Phase 1 stock research."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.market import Timeframe


class AnalyzeRequest(BaseModel):
    """One immutable analysis request; historical timestamps must be timezone aware."""

    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(
        min_length=1, max_length=20, pattern=r"^[A-Za-z0-9._-]+$", default=None
    )
    symbols: list[str] = Field(default_factory=list, max_length=5)
    timeframe: Timeframe = "1D"
    question: str = Field(default="Phân tích kỹ thuật mã này.", min_length=3, max_length=2000)
    analyzed_at: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(valuestr.strip().upper() for valuestr in values))

    @field_validator("timeframe", mode="before")
    @classmethod
    def normalize_timeframe(cls, value: str | None) -> str | None:
        return {"D1": "1D", "W1": "1W"}.get(value.upper(), value.upper()) if value else None

    @field_validator("analyzed_at")
    @classmethod
    def aware_analysis_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("analyzed_at must include a timezone")
        if value is not None and value > datetime.now(UTC):
            raise ValueError("analyzed_at cannot be in the future")
        return value


class TechnicalFinding(BaseModel):
    """Evidence produced by a deterministic node, without an invented confidence score."""

    signal: str
    direction: Literal["bullish", "bearish", "neutral"] = "neutral"
    strength: str = "unknown"
    observations: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    as_of: datetime | None = None
    is_confirmed: bool = True


class StockReport(BaseModel):
    """LLM synthesis constrained to technical evidence already in the graph state."""

    market_overview: str
    volume_analysis: str
    momentum_analysis: str
    trend_market_structure: str
    divergence_analysis: str
    historical_similarities: str = "Chưa có bằng chứng phù hợp."
    news_context: str = "Chưa có bằng chứng phù hợp."
    bullish_evidence: list[str] = Field(default_factory=list)
    bearish_evidence: list[str] = Field(default_factory=list)
    risks_and_invalidation: list[str] = Field(default_factory=list)
    final_conclusion: str
    references: list[str] = Field(default_factory=list)


AssessmentStatus = Literal["positive", "negative", "neutral", "unknown"]
ReportStance = Literal["bullish", "bearish", "mixed", "neutral"]


class IndicatorAssessment(BaseModel):
    """One normalized indicator row rendered as a table by the chat client.

    ``status`` is the directional assessment; ``state`` keeps non-directional
    facts (zone, volatility regime, alignment) so volume/BB are not forced into
    bullish/bearish labels. ``signal_phase`` separates an early signal from an
    indicator-confirmed or price-confirmed one.
    """

    key: str
    label: str
    value: str | None = None
    status: AssessmentStatus = "unknown"
    note: str = ""
    signal_phase: Literal["early", "indicator_confirmed", "price_confirmed"] | None = None
    confirmation_level: Literal["early", "indicator", "price"] | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WatchLevel(BaseModel):
    """A price level with a plain-language role, not an internal structure field."""

    label: str
    price: float
    note: str = ""


class NewsItem(BaseModel):
    """One attributable news source used as market/sector context."""

    title: str
    publisher: str = ""
    published_at: str = ""
    url: str
    source_type: str = "news"
    scope: Literal["company", "market"] = "company"


class AnalysisReportCard(BaseModel):
    """Frontend-ready JSON derived deterministically from graph evidence.

    Narrative fields come from the LLM after term normalization; indicator
    statuses and values are computed here from the closed-candle snapshot so the
    UI never has to interpret raw engine identifiers.
    """

    version: Literal["research-report-v1"] = "research-report-v1"
    symbols: list[str] = Field(default_factory=list)
    timeframe: str
    as_of: str
    price: float | None = None
    change_pct: float | None = None
    stance: ReportStance = "neutral"
    stance_label: str = "Trung tính"
    summary: str
    indicators: list[IndicatorAssessment] = Field(default_factory=list)
    supports: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    watch_levels: list[WatchLevel] = Field(default_factory=list)
    history: str = "Chưa có case tương tự."
    news: str = "Chưa có tin tức phù hợp."
    news_items: list[NewsItem] = Field(default_factory=list)
    data_note: str = ""
    data_freshness: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Current checkpoint projection; details stay available after process restart."""

    analysis_id: UUID
    symbol: str
    timeframe: Timeframe
    status: Literal["running", "completed", "failed"]
    analyzed_at: datetime
    market_snapshot: dict[str, Any] = Field(default_factory=dict)
    volume_analysis: TechnicalFinding | None = None
    momentum_analysis: TechnicalFinding | None = None
    trend_analysis: TechnicalFinding | None = None
    divergence_analysis: TechnicalFinding | None = None
    final_report: StockReport | None = None
    report_card: AnalysisReportCard | None = None
    symbols: list[str] = Field(default_factory=list)
    technical_summary: dict[str, Any] = Field(default_factory=dict)
    historical_evidence: list[dict[str, Any]] = Field(default_factory=list)
    news_evidence: list[dict[str, Any]] = Field(default_factory=list)
    markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisStep(BaseModel):
    """Nodes scheduled by a persisted graph super-step."""

    nodes: list[str]
    created_at: datetime | None = None
