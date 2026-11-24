"""Deterministic request resolution and evidence comparison for research runs."""

import re
from datetime import datetime, time, timedelta
from typing import Any

from app.core.database import session_scope
from app.core.exceptions import InvalidRequestError
from app.repositories.market_repository import MarketRepository
from app.schemas.agent import AnalyzeRequest

RULE_VERSION = "research-v1"


async def resolve_request(request: AnalyzeRequest, market_tz, *, follow_up: str | None = None):
    """Resolve validated symbols and a point-in-time cutoff before admitting work."""

    def tokens(text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]{2,11}\b", text.upper())))

    ignored = {"RSI", "MACD", "ADX", "DMI", "OBV", "ATR", "SMA", "EMA", "BOS", "MSS", "RAG"}
    symbols = []
    explicit = request.symbols or ([request.symbol] if request.symbol else [])
    async with session_scope() as session:
        repo = MarketRepository(session)
        for token in (
            explicit or [token for token in tokens(request.question) if token not in ignored][:30]
        ):
            if await repo.get_symbol(token):
                symbols.append(token)
            elif explicit:
                raise InvalidRequestError(f"Không tìm thấy mã {token}.")
        if not symbols and follow_up:
            for token in [token for token in tokens(follow_up) if token not in ignored][:30]:
                if await repo.get_symbol(token):
                    symbols.append(token)
    if not symbols or len(symbols) > 5:
        raise InvalidRequestError("Vui lòng chỉ rõ từ 1 đến 5 mã cổ phiếu.")
    timeframe = (
        request.timeframe
        if "timeframe" in request.model_fields_set
        else ("1W" if re.search(r"\b(?:W1|1W)\b|\btuần\b", request.question, re.I) else "1D")
    )
    analyzed_at = request.analyzed_at
    if analyzed_at is None:
        date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", request.question)
        if date_match:
            value = date_match.group()
            try:
                day = datetime.strptime(value, "%Y-%m-%d" if "-" in value else "%d/%m/%Y").date()
                now = datetime.now(market_tz)
                if day > now.date():
                    raise ValueError
                analyzed_at = min(datetime.combine(day, time.max, market_tz), now)
            except ValueError as exc:
                raise InvalidRequestError(
                    "Ngày phân tích không hợp lệ hoặc nằm trong tương lai."
                ) from exc
    return symbols, timeframe, analyzed_at


def fingerprint(snapshot: dict[str, Any]) -> dict[str, str]:
    """Keep missing values out of comparisons instead of counting them as matches."""
    result = {}
    for item in snapshot.get("indicator_values", {}).values():
        name = item["indicator"]
        value = item.get("values", {})
        signal = item.get("signal")
        if signal and signal != "insufficient_data":
            result[name] = str(signal)
        if name == "macd" and (hist := value.get("histogram")) is not None:
            result["macd"] = "positive" if hist > 0 else "negative" if hist < 0 else "flat"
        if name == "adx" and (adx := value.get("adx")) is not None:
            result["adx"] = "strong" if adx >= 25 else "weak"
        if name == "rvol" and (rvol := value.get("rvol")) is not None:
            result["rvol"] = "above_average" if rvol >= 1.5 else "normal"
        if name == "rsi" and (rsi := value.get("rsi")) is not None:
            result["rsi"] = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "midrange"
            prior = item.get("previous_values", {}).get("rsi")
            if prior is not None:
                result["rsi_slope"] = (
                    "rising" if rsi > prior else "falling" if rsi < prior else "flat"
                )
    values = {
        item["indicator"]: item.get("values", {})
        for item in snapshot.get("indicator_values", {}).values()
    }
    pdi = values.get("plus_di", {}).get("plus_di")
    mdi = values.get("minus_di", {}).get("minus_di")
    if pdi is not None and mdi is not None:
        result["dmi"] = "bullish" if pdi > mdi else "bearish" if pdi < mdi else "neutral"
    bands = values.get("bollinger", {})
    middle, upper, lower = bands.get("middle"), bands.get("upper"), bands.get("lower")
    if middle and upper is not None and lower is not None:
        result["bollinger_width"] = "narrow" if (upper - lower) / abs(middle) < 0.05 else "normal"
    ma = {
        item.get("parameters", {}).get("period"): item.get("values", {}).get("sma")
        for item in snapshot.get("indicator_values", {}).values()
        if item["indicator"] == "sma"
    }
    if ma.get(20) is not None and ma.get(50) is not None:
        result["ma_alignment"] = (
            "bullish" if ma[20] > ma[50] else "bearish" if ma[20] < ma[50] else "neutral"
        )
    if trend := snapshot.get("structure", {}).get("confirmed_trend"):
        result["market_structure"] = trend
    events = snapshot.get("divergence_events", [])
    if events:
        last = events[-1]
        oscillators = sorted({str(item.get("indicator")) for item in last.get("oscillators", [])})
        result["divergence"] = ":".join(
            [str(last.get("direction")), str(last.get("kind")), *oscillators]
        )
    return result


def case_fingerprint(snapshot: dict[str, Any]) -> dict[str, str]:
    """Normalize verified case snapshots using the current rules."""
    return fingerprint(
        {
            "indicator_values": {
                key: {**value, "indicator": key}
                for key, value in snapshot.get("indicators", {}).items()
            },
            "structure": snapshot.get("market_structure", {}),
            "divergence_events": snapshot.get("divergence_events", []),
        }
    )


class ResearchContext:
    """Adapt the existing knowledge and news services to the research graph."""

    def __init__(self, runtime, settings):
        self.runtime = runtime
        self.settings = settings

    async def cases(self, state):
        from app.repositories.evidence_repository import EvidenceRepository
        from app.schemas.analysis import AnalysisSearch
        from app.services.rag_query_service import RagQueryService

        if not self.settings.agent_rag_enabled:
            return []
        if self.runtime is None:
            raise RuntimeError("RAG runtime unavailable")
        cutoff = datetime.fromisoformat(state["analyzed_at"])
        matches = []
        async with session_scope() as session:
            service = RagQueryService(self.runtime, session)
            repository = EvidenceRepository(session)
            for symbol, technical in state["technical_summary"].items():
                pattern = technical["fingerprint"]
                if not pattern:
                    continue
                result = await service.search(
                    AnalysisSearch(
                        query=" ".join(f"{k}: {v}" for k, v in pattern.items()),
                        timeframe=state["timeframe"],
                        before=cutoff,
                        top_k=20,
                    ),
                    rerank_top_n=20,
                )
                for item in result.data:
                    case = item.case
                    if (
                        case.created_at > cutoff
                        or case.analyzed_at is None
                        or case.analyzed_at >= cutoff
                    ):
                        continue
                    evidence = await repository.state(case.id)
                    technical_snapshot = evidence.technical_snapshot if evidence else {}
                    if technical_snapshot.get("status") != "ok":
                        continue
                    verified_at = technical_snapshot.get("as_of")
                    if not verified_at or datetime.fromisoformat(verified_at) > case.analyzed_at:
                        continue
                    stored = (
                        technical_snapshot.get("research_fingerprint")
                        if technical_snapshot.get("research_rule_version") == RULE_VERSION
                        else None
                    ) or case_fingerprint(technical_snapshot)
                    common = pattern.keys() & stored.keys()
                    same = sorted(k for k in common if pattern[k] == stored[k])
                    if len(same) < 2 or ("divergence" in pattern and "divergence" not in same):
                        continue
                    from app.models.analysis_case import AnalysisCase

                    original = await session.get(AnalysisCase, case.id)
                    outcome = (
                        original.outcome
                        if original
                        and original.outcome_recorded_at
                        and original.outcome_recorded_at <= cutoff
                        else None
                    )
                    matches.append(
                        {
                            "evidence_id": f"case:{case.id}",
                            "symbol": case.symbol,
                            "compared_to": symbol,
                            "analyzed_at": case.analyzed_at.isoformat(),
                            "matched_patterns": same,
                            "different_patterns": sorted(common - set(same)),
                            "missing_patterns": sorted(pattern.keys() - stored.keys()),
                            "score": len(same) / len(pattern),
                            "user_thesis": case.thesis[:1500],
                            "source": case.source,
                            "historical_outcome": outcome,
                            "source_url": f"/api/v1/analysis/cases/{case.id}",
                        }
                    )
        return sorted(matches, key=lambda row: (-row["score"], row["evidence_id"]))[:5]

    async def news(self, state):
        if not self.settings.agent_news_enabled:
            return []
        if self.runtime is None:
            raise RuntimeError("RAG runtime unavailable")
        cutoff = datetime.fromisoformat(state["analyzed_at"])
        articles = {}
        sectors: list[str] = []
        for symbol in state.get("symbols") or [state["symbol"]]:
            async with session_scope() as session:
                record = await MarketRepository(session).get_symbol(symbol)
                company = record.company_name if record else ""
                sector = record.sector if record else ""
            if sector and sector not in sectors:
                sectors.append(sector)
            results: list = []
            for days in (30, 90):
                results, _ = await self.runtime.news.enrich(
                    symbol,
                    cutoff,
                    company=company or "",
                    sector=sector or "",
                    window_start=cutoff - timedelta(days=days),
                )
                if results:
                    break
            for item in results:
                if item.published_at > cutoff or (item.updated_at and item.updated_at > cutoff):
                    continue
                data = item.model_dump(mode="json")
                url = data.get("canonical_url") or data.get("source_url")
                if url:
                    articles[url] = {
                        **data,
                        "evidence_id": url,
                        "content": data.get("content", "")[:1500],
                    }
        # A separate broad query supplies market/macro context. It must not require
        # the ticker to appear, otherwise an overall market article is discarded.
        market_results, _ = await self.runtime.news.enrich(
            "MARKET",
            cutoff,
            sector=sectors[0] if sectors else "",
            query="thị trường chứng khoán Việt Nam VN-Index vĩ mô dòng tiền",
            require_terms=["thị trường", "chứng khoán", "VN-Index", "vĩ mô", "dòng tiền"],
            min_relevance=self.settings.rag_news_min_relevance,
            window_start=cutoff - timedelta(days=14),
            associated_symbol="MARKET",
        )
        for item in market_results:
            if item.published_at > cutoff or (item.updated_at and item.updated_at > cutoff):
                continue
            data = item.model_dump(mode="json")
            url = data.get("canonical_url") or data.get("source_url")
            if url:
                articles[url] = {
                    **data,
                    "evidence_id": url,
                    "content": data.get("content", "")[:1500],
                }
        return list(articles.values())[:10]


def render_markdown(state, report):
    """Render a human-readable report from the normalized card, not raw engine keys."""
    card = state.get("report_card") or {}
    symbols = card.get("symbols") or state.get("symbols") or [state["symbol"]]
    blocks = ["# Phân tích " + ", ".join(symbols)]
    if card.get("as_of"):
        blocks.append(f"Dữ liệu nến đã đóng: {card['as_of']}")
    if card.get("price") is not None:
        price = f"{card['price']:,.0f}".replace(",", ".")
        change = card.get("change_pct")
        suffix = f" ({change:+.2f}%)".replace(".", ",") if change is not None else ""
        blocks.append(f"Giá đóng cửa: {price}{suffix}")
    if card.get("indicators"):
        icons = {"positive": "✓", "negative": "✕", "neutral": "•", "unknown": "?"}
        table = [
            "## Bảng chỉ báo",
            "",
            "| Chỉ báo | Đánh giá | Ghi chú |",
            "| --- | --- | --- |",
        ]
        for row in card["indicators"]:
            icon = icons.get(row.get("status"), "?")
            note = str(row.get("note") or "").replace("|", "\\|")
            table.append(f"| {row.get('label')} | {icon} | {note} |")
        blocks.append("\n".join(table))
    blocks.append("## Nhận định\n\n" + (card.get("summary") or report.final_conclusion))
    if card.get("supports"):
        blocks.append("## Điểm hỗ trợ\n\n" + "\n".join(f"- {item}" for item in card["supports"]))
    if card.get("risks"):
        blocks.append(
            "## Rủi ro & điều kiện theo dõi\n\n" + "\n".join(f"- {item}" for item in card["risks"])
        )
    if card.get("watch_levels"):
        levels = []
        for level in card["watch_levels"]:
            price = f"{level['price']:,.0f}".replace(",", ".")
            levels.append(f"- {level.get('label')}: {price}. {level.get('note') or ''}".strip())
        blocks.append("## Mốc giá cần theo dõi\n\n" + "\n".join(levels))
    if card.get("history") and card["history"] != "Chưa có case tương tự.":
        blocks.append("## Case tương tự\n\n" + card["history"])
    if card.get("news") and card["news"] != "Chưa có tin tức phù hợp.":
        blocks.append("## Tin tức\n\n" + card["news"])
    if card.get("news_items"):
        items = []
        for item in card["news_items"]:
            source = ", ".join(
                part for part in (item.get("publisher"), item.get("published_at")) if part
            )
            items.append(
                f"- [{item.get('title')}]({item.get('url')})" + (f" — {source}" if source else "")
            )
        blocks.append("## Nguồn tin tham khảo\n\n" + "\n".join(items))
    if card.get("data_note"):
        blocks.append(card["data_note"])
    return "\n\n".join(blocks)


def evaluate_signals(snapshot: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    """Separate indicator evidence and direction from strength or alert zones."""
    states = fingerprint(snapshot)
    market = snapshot["market_snapshot"]
    rows = []
    all_values = {
        item["indicator"]: item.get("values", {}) for item in snapshot["indicator_values"].values()
    }
    for item in snapshot["indicator_values"].values():
        name = item["indicator"]
        values = item.get("values", {})
        direction = "neutral"
        alerts = []
        explanation = states.get(name, item.get("signal") or "insufficient_data")
        if name == "rsi":
            if explanation in {"overbought", "oversold"}:
                alerts.append(explanation)
        elif name == "adx":
            positive = all_values.get("plus_di", {}).get("plus_di")
            negative = all_values.get("minus_di", {}).get("minus_di")
            if positive is not None and negative is not None:
                direction = (
                    "bullish"
                    if positive > negative
                    else "bearish"
                    if positive < negative
                    else "neutral"
                )
                values = {**values, "plus_di": positive, "minus_di": negative}
            explanation = f"Sức mạnh ADX: {explanation}. Hướng theo +DI và -DI."
        elif name == "macd":
            direction = {"positive": "bullish", "negative": "bearish"}.get(explanation, "neutral")
        elif name == "bollinger":
            middle = values.get("middle")
            upper, lower = values.get("upper"), values.get("lower")
            close = market.get("close")
            if middle and upper is not None and lower is not None and close is not None:
                bandwidth = (upper - lower) / abs(middle)
                values = {**values, "bandwidth": bandwidth}
                direction = (
                    "bullish" if close > middle else "bearish" if close < middle else "neutral"
                )
                if bandwidth < 0.05:
                    alerts.append("narrow_band")
                explanation = "Giá so với đường giữa và độ rộng dải."
        elif name == "ichimoku":
            a, b = values.get("senkou_a"), values.get("senkou_b")
            close = market.get("close")
            if a is not None and b is not None and close is not None:
                direction = (
                    "bullish"
                    if close > max(a, b)
                    else "bearish"
                    if close < min(a, b)
                    else "neutral"
                )
                explanation = "Vị trí giá so với mây tại nến đang xét."
        rows.append(
            {
                "indicator": name,
                "parameters": item.get("parameters", {}),
                "values": values,
                "direction": direction,
                "alerts": alerts,
                "explanation": explanation,
                "timeframe": timeframe,
                "as_of": market["data_timestamp"],
                "confirmation_status": "CONFIRMED",
                "rule_version": RULE_VERSION,
            }
        )
    return rows
