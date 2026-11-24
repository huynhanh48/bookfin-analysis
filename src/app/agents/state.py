"""Small serializable state for LangGraph checkpoints."""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage

from app.schemas.market import Timeframe


def merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Merge disjoint node timings emitted in the same parallel super-step."""
    return {**left, **right}


class AnalysisState(TypedDict, total=False):
    """Each node owns distinct keys so parallel updates never conflict."""

    analysis_id: str
    user_id: str
    symbols: list[str]
    technical_summary: dict[str, Any]
    market_by_symbol: dict[str, Any]
    markdown: str
    enrichment_metadata: Annotated[dict[str, Any], merge_metadata]
    symbol: str
    timeframe: Timeframe
    analyzed_at: str
    question: str
    chat_history: list[BaseMessage]
    include_live: bool
    market_snapshot: dict[str, Any]
    indicator_values: dict[str, Any]
    structure: dict[str, Any]
    divergence_events: list[dict[str, Any]]
    volume_analysis: dict[str, Any]
    momentum_analysis: dict[str, Any]
    trend_analysis: dict[str, Any]
    divergence_analysis: dict[str, Any]
    final_report: dict[str, Any]
    report_card: dict[str, Any]
    warnings: list[str]
    historical_evidence: list[dict[str, Any]]
    news_evidence: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    execution_metadata: Annotated[dict[str, Any], merge_metadata]
