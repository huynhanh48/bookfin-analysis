"""Compact decision-time representation; outcomes and full articles are never embedded."""

import json
from collections import Counter

from app.schemas.evidence import TechnicalClaim, VisionObservations


def _divergence_text(technical: dict) -> str:
    """Summarize recent divergence direction and RSI context without dumping the full series."""
    rows = []
    for event in technical.get("divergence_events", [])[-3:]:
        rsi = [
            oscillator.get("rsi_context", {})
            for oscillator in event.get("oscillators", [])
            if oscillator.get("indicator") == "RSI"
        ]
        rows.append(
            {
                "direction": event.get("direction"),
                "confirmed_at": event.get("confirmed_at"),
                "rsi": rsi[0] if rsi else None,
            }
        )
    return json.dumps(rows, default=str, ensure_ascii=False) if rows else "none"


def normalize_analysis(
    original: str,
    context: dict,
    observations: VisionObservations,
    claims: list[TechnicalClaim],
    technical: dict,
    news: list,
) -> tuple[str, dict]:
    """Retain contradictions/uncertainty and source references in bounded retrieval text."""
    summary = dict(Counter(claim.verification_status.value for claim in claims))
    parts = [
        f"Symbol: {context.get('symbol')}; Timeframe: {context.get('timeframe')}; Analysis date: {context.get('analysis_at')}",
        f"Original thesis: {original[:12_000]}",
        f"Vision observations (unverified): {observations.description[:4000]}",
        f"Vision uncertainties: {observations.uncertainties}",
        f"Verified technical measurements: {json.dumps(technical.get('filter_values', {}), ensure_ascii=False)}",
        f"Market structure: trend={technical.get('market_structure', {}).get('confirmed_trend')}; swings={json.dumps(technical.get('market_structure', {}).get('swings', [])[-6:], default=str, ensure_ascii=False)}; events={json.dumps(technical.get('structure_events', [])[-3:], default=str, ensure_ascii=False)}",
        f"Divergence context (historical, unverified): {_divergence_text(technical)}",
    ]
    for claim in claims:
        parts.append(
            f"{claim.verification_status.value}: {claim.original_text}; {claim.verification_reason}; evidence={claim.evidence_ids}"
        )
    for article in news:
        parts.append(
            f"News context (source interpretation, not technical proof): {article.title}; published={article.published_at.isoformat()}; source={article.canonical_url}; excerpt={article.content[:500]}"
        )
    return "\n".join(parts), summary
