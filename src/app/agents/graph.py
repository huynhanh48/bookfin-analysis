"""Deterministic LangGraph workflow; only the final synthesis calls an LLM."""

import asyncio
import json
from datetime import datetime
from time import perf_counter
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import SecretStr

from app.agents.report import (
    build_report_card,
    humanize_report,
    prompt_evidence,
    prompt_glossary,
)
from app.agents.research import RULE_VERSION, evaluate_signals, fingerprint, render_markdown
from app.agents.state import AnalysisState
from app.schemas.agent import StockReport, TechnicalFinding


class EvidenceLoader(Protocol):
    """Source of one confirmed as-of market and indicator snapshot."""

    async def load(
        self, symbol: str, timeframe: str, analyzed_at: datetime, include_live: bool = False
    ) -> dict[str, Any]: ...


class ReportSynthesizer(Protocol):
    """Structured-output LLM boundary, replaceable in deterministic tests."""

    async def synthesize(self, evidence: dict[str, Any]) -> StockReport: ...


class UnconfiguredReportSynthesizer:
    """Allow checkpoint reads when an OpenRouter key has been rotated away."""

    async def synthesize(self, evidence: dict[str, Any]) -> StockReport:
        raise RuntimeError("Agent model is not configured")


class OpenRouterReportSynthesizer:
    """Use ChatOpenRouter solely to explain already calculated evidence."""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float):
        from langchain_openrouter import ChatOpenRouter

        self._timeout = timeout
        self._chain = ChatOpenRouter(
            api_key=SecretStr(api_key),
            base_url=base_url,
            model=model,
            temperature=0,
            timeout=max(1, int(timeout * 1000)),
            max_retries=1,
        ).with_structured_output(StockReport, method="function_calling")

    async def synthesize(self, evidence: dict[str, Any]) -> StockReport:
        """Reject unexpected unstructured provider output."""
        system = (
            "Bạn là chuyên viên phân tích kỹ thuật chứng khoán Việt Nam, viết cho nhà đầu tư cá nhân. "
            "Chỉ được dùng JSON bằng chứng đã cung cấp; không tự tính lại chỉ báo, không bịa giá, "
            "ngày, tin tức, lịch sử hay xác suất. Nêu rõ khi bằng chứng mâu thuẫn hoặc còn thiếu. "
            "Không bắt buộc phải đưa khuyến nghị mua/bán. "
            "Quy tắc trình bày: (1) Không viết mã nội bộ, tên key JSON hay trạng thái kỹ thuật thô; "
            "dịch theo glossary rồi diễn giải ý nghĩa. (2) Không liệt kê chuỗi swing, mã sự kiện "
            "hoặc hàng loạt con số thô; chỉ nêu 1-2 mốc quan trọng kèm ý nghĩa. "
            "(3) Viết câu ngắn, giọng chuyên gia gần gũi, giải thích thuật ngữ lần đầu xuất hiện. "
            "(4) Tập trung vào: xu hướng, động lượng, dòng tiền, rủi ro và điều kiện theo dõi. "
            "(5) Khi có news_evidence, tổng hợp bối cảnh doanh nghiệp, ngành và toàn thị trường "
            "thành một đoạn ngắn; chỉ dùng tiêu đề/ngày/nguồn có trong dữ liệu, không bịa tin. "
            f"Glossary bắt buộc: {prompt_glossary()}. "
            "Trả lời bằng tiếng Việt theo schema."
        )
        history = evidence.get("chat_history", [])
        current_evidence = prompt_evidence(evidence)
        async with asyncio.timeout(self._timeout):
            result = await self._chain.ainvoke(
                [
                    SystemMessage(content=system + " Lịch sử chỉ dùng để hiểu ngữ cảnh."),
                    *history,
                    HumanMessage(content=json.dumps(current_evidence, ensure_ascii=False)),
                ]
            )
        return StockReport.model_validate(result)


def _indicator(state: AnalysisState, name: str) -> dict[str, Any]:
    return next(
        (
            value
            for value in state.get("indicator_values", {}).values()
            if value.get("indicator") == name
        ),
        {},
    )


def build_analysis_graph(
    loader: EvidenceLoader, synthesizer: ReportSynthesizer, checkpointer=None, context=None
):
    """Build a fan-out/fan-in graph with disjoint writes at each parallel step."""

    async def market(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        snapshots = {}
        for symbol in state.get("symbols") or [state["symbol"]]:
            snapshots[symbol] = await loader.load(
                symbol,
                state["timeframe"],
                datetime.fromisoformat(state["analyzed_at"]),
                include_live=state.get("include_live", False),
            )
        return {
            **snapshots[state["symbol"]],
            "market_by_symbol": snapshots,
            "execution_metadata": {"market_ms": round((perf_counter() - started) * 1000, 2)},
        }

    async def volume(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        snapshot = state["market_snapshot"]
        rvol = _indicator(state, "rvol").get("values", {}).get("rvol")
        average = _indicator(state, "volume_sma").get("values", {}).get("volume_sma")
        signal = _indicator(state, "rvol").get("signal") or "insufficient_data"
        previous_close = snapshot.get("previous_close")
        previous_volume = snapshot.get("previous_volume")
        price_up = previous_close is not None and snapshot.get("close", 0) > previous_close
        price_down = previous_close is not None and snapshot.get("close", 0) < previous_close
        volume_up = previous_volume is not None and snapshot["volume"] > previous_volume
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish"
            if price_up and volume_up
            else "bearish"
            if price_down and volume_up
            else "neutral"
        )
        relationship = (
            "Giá tăng cùng khối lượng tăng."
            if price_up and volume_up
            else "Giá giảm cùng khối lượng tăng."
            if price_down and volume_up
            else "Giá/khối lượng chưa đồng thuận rõ."
        )
        finding = TechnicalFinding(
            signal=signal,
            direction=direction,
            strength="above_average" if signal == "above_average" else "unknown",
            observations=[
                "Khối lượng cao hơn mức nền."
                if signal == "above_average"
                else "Chưa có xác nhận khối lượng mạnh.",
                relationship,
            ],
            evidence={
                "volume": snapshot["volume"],
                "volume_sma_20": average,
                "rvol_20": rvol,
                "obv": _indicator(state, "obv").get("values", {}).get("obv"),
                "obv_previous": _indicator(state, "obv").get("previous_values", {}).get("obv"),
                "previous_volume": previous_volume,
            },
            as_of=datetime.fromisoformat(snapshot["data_timestamp"]),
        )
        return {
            "volume_analysis": finding.model_dump(mode="json"),
            "execution_metadata": {"volume_ms": round((perf_counter() - started) * 1000, 2)},
        }

    async def momentum(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        snapshot = state["market_snapshot"]
        rsi = _indicator(state, "rsi")
        macd = _indicator(state, "macd")
        rsi_value = rsi.get("values", {}).get("rsi")
        histogram = macd.get("values", {}).get("histogram")
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish"
            if histogram is not None and histogram > 0
            else ("bearish" if histogram is not None and histogram < 0 else "neutral")
        )
        finding = TechnicalFinding(
            signal=macd.get("signal") or rsi.get("signal") or "insufficient_data",
            direction=direction,
            observations=[f"RSI: {rsi_value}" if rsi_value is not None else "RSI chưa đủ dữ liệu"],
            evidence={
                "rsi": rsi_value,
                "macd": macd.get("values", {}),
                "rsi_signal": rsi.get("signal"),
            },
            as_of=datetime.fromisoformat(snapshot["data_timestamp"]),
        )
        return {
            "momentum_analysis": finding.model_dump(mode="json"),
            "execution_metadata": {"momentum_ms": round((perf_counter() - started) * 1000, 2)},
        }

    async def trend(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        structure = state["structure"]
        trend_value = structure["confirmed_trend"]
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish"
            if trend_value == "UPTREND"
            else "bearish"
            if trend_value == "DOWNTREND"
            else "neutral"
        )
        finding = TechnicalFinding(
            signal=trend_value,
            direction=direction,
            observations=[f"Xu hướng cấu trúc đã xác nhận: {trend_value}"],
            evidence={
                "structure": structure,
                "sma_20": _indicator(state, "sma").get("values"),
                "adx": _indicator(state, "adx").get("values"),
                "bollinger": _indicator(state, "bollinger").get("values"),
            },
            as_of=datetime.fromisoformat(state["market_snapshot"]["data_timestamp"]),
        )
        return {
            "trend_analysis": finding.model_dump(mode="json"),
            "execution_metadata": {"trend_ms": round((perf_counter() - started) * 1000, 2)},
        }

    async def divergence(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        events = state.get("divergence_events", [])
        latest = events[-1] if events else None
        finding = TechnicalFinding(
            signal="historical_confirmed_divergence" if latest else "none_confirmed_in_window",
            direction=latest["direction"] if latest else "neutral",
            observations=[f"Phân kỳ đã xác nhận tại {latest['confirmed_at']}."] if latest else [],
            evidence={"latest": latest, "event_count": len(events)},
            as_of=datetime.fromisoformat(state["market_snapshot"]["data_timestamp"]),
        )
        return {
            "divergence_analysis": finding.model_dump(mode="json"),
            "execution_metadata": {"divergence_ms": round((perf_counter() - started) * 1000, 2)},
        }

    async def technical_summary(state: AnalysisState) -> dict[str, Any]:
        return {
            "technical_summary": {
                symbol: {
                    "market": snapshot["market_snapshot"],
                    "indicators": snapshot["indicator_values"],
                    "structure": snapshot["structure"],
                    "divergence": snapshot["divergence_events"],
                    "fingerprint": fingerprint(snapshot),
                    "rule_version": RULE_VERSION,
                    "findings": evaluate_signals(snapshot, state["timeframe"]),
                }
                for symbol, snapshot in state["market_by_symbol"].items()
            }
        }

    async def enrich(
        state: AnalysisState, kind: str, key: str, timeout: float = 15
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                return {key: await getattr(context, kind)(state) if context else []}
        except Exception:
            return {key: [], "enrichment_metadata": {kind: "unavailable"}}

    async def historical(state: AnalysisState) -> dict[str, Any]:
        return await enrich(state, "cases", "historical_evidence")

    async def news(state: AnalysisState) -> dict[str, Any]:
        # News search + extraction is slower than a DB case lookup.
        return await enrich(state, "news", "news_evidence", timeout=45)

    async def conclusion(state: AnalysisState) -> dict[str, Any]:
        started = perf_counter()
        if "technical_summary" not in state:
            state = {
                **state,
                "technical_summary": {
                    state["symbol"]: {
                        "market": state["market_snapshot"],
                        "indicators": state["indicator_values"],
                        "fingerprint": fingerprint(dict(state)),
                    }
                },
            }
        references = [
            f"fcdata:{state['symbol']}:{state['market_snapshot']['data_timestamp']}",
            "indicator_engine:ta-lib",
            "market_structure:confirmed",
        ]
        evidence: dict[str, Any] = {
            "question": state["question"],
            "market": state["market_snapshot"],
            "volume": state["volume_analysis"],
            "momentum": state["momentum_analysis"],
            "trend": state["trend_analysis"],
            "divergence": state["divergence_analysis"],
            "references": references,
            "historical_evidence": state.get("historical_evidence", []),
            "technical_summary": state["technical_summary"],
            "news_evidence": state.get("news_evidence", []),
        }
        warnings = list(state.get("warnings", []))
        warnings.extend(f"{key}_unavailable" for key in state.get("enrichment_metadata", {}))
        references.extend(
            row["evidence_id"]
            for row in state.get("historical_evidence", []) + state.get("news_evidence", [])
        )
        evidence["references"] = references
        if state.get("chat_history"):
            evidence["chat_history"] = state["chat_history"]
        try:
            report = await synthesizer.synthesize(evidence)
        except Exception:
            if context is None:
                raise
            warnings.append("model_unavailable")
            report = StockReport(
                market_overview="Dữ liệu kỹ thuật đã được tính.",
                volume_analysis=state["volume_analysis"]["signal"],
                momentum_analysis=state["momentum_analysis"]["signal"],
                trend_market_structure=state["trend_analysis"]["signal"],
                divergence_analysis=state["divergence_analysis"]["signal"],
                final_conclusion="Chưa có diễn giải từ mô hình. Xem dữ kiện theo từng mã ở trên.",
            )
        for key, field in (
            ("historical_evidence", "historical_similarities"),
            ("news_evidence", "news_context"),
        ):
            if not state.get(key):
                setattr(report, field, "Chưa có bằng chứng phù hợp.")
        report.references = list(dict.fromkeys(references))
        report = humanize_report(report)
        card = build_report_card(state, report)
        return {
            "final_report": report.model_dump(mode="json"),
            "report_card": card.model_dump(mode="json"),
            "markdown": render_markdown(
                {**state, "report_card": card.model_dump(mode="json")}, report
            ),
            "warnings": warnings,
            "execution_metadata": {"conclusion_ms": round((perf_counter() - started) * 1000, 2)},
        }

    graph = StateGraph(AnalysisState)
    graph.add_node("market", market)
    graph.add_node("volume", volume)
    graph.add_node("momentum", momentum)
    graph.add_node("trend", trend)
    graph.add_node("divergence", divergence)
    graph.add_node("technical_summary", technical_summary)
    graph.add_node("historical", historical)
    graph.add_node("news", news)
    graph.add_node("conclusion", conclusion)
    graph.add_edge(START, "market")
    for name in ("volume", "momentum", "trend", "divergence"):
        graph.add_edge("market", name)
    graph.add_edge(["volume", "momentum", "trend", "divergence"], "technical_summary")
    graph.add_edge("technical_summary", "historical")
    graph.add_edge("technical_summary", "news")
    graph.add_edge(["historical", "news"], "conclusion")
    graph.add_edge("conclusion", END)
    return graph.compile(checkpointer=checkpointer)
