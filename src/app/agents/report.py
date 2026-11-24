"""Normalize technical evidence into a frontend-ready report card.

The UI renders tables and status icons from this JSON. Values and statuses are
computed from the closed-candle snapshot, never from the language model, so the
table stays consistent with the indicator engine. The LLM only supplies the
narrative fields after :func:`humanize_report` removes internal identifiers.
"""

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.assessment import AssessmentContext, enrich_rows
from app.core.config import get_settings
from app.schemas.agent import (
    AnalysisReportCard,
    AssessmentStatus,
    IndicatorAssessment,
    NewsItem,
    StockReport,
    WatchLevel,
)

# Longest identifiers first so BOS_BULLISH is not partially rewritten by BULLISH.
_GLOSSARY: tuple[tuple[str, str], ...] = (
    (r"\bBREAK_BIAS\b", "cấu trúc"),
    (r"\bbreak_bias\b", "cấu trúc"),
    (r"MACD histogram", "động lượng MACD"),
    (r"\bhistogram\b", "động lượng"),
    (r"\btenkan\b", "đường chuyển đổi"),
    (r"\bkijun\b", "đường cơ sở"),
    (r"mâu thuẫn nội tại", "tín hiệu chưa đồng thuận"),
    (r"\bCLOSED_ONLY\b", "chỉ dùng nến đã đóng"),
    (r"\bHISTORICAL\b", "dữ liệu lịch sử"),
    (r"\brealtime_price\b", "giá trong phiên"),
    (r"\bCONFIRMED\b", "đã xác nhận"),
    (r"\bnull\b", "chưa có"),
)


_ISO_DATETIME = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))\b"
)
_MA_LEVEL = re.compile(
    r"\bMA\s*(20|50)\s*(?:\([^)]*\)|(?:quanh|tại|ở)\s+[\d.,]+|\s+[\d.,]{3,})",
    re.IGNORECASE,
)
_MA_TEXT_LEVEL = re.compile(
    r"(đường trung bình (?:20|50) phiên)\s*(?:\([^)]*\)|(?:quanh|tại|ở)\s+[\d.,]+|\s+[\d.,]{3,})",
    re.IGNORECASE,
)

_EVENT_LABELS = {
    "BOS_BULLISH": "Phá vỡ cấu trúc tăng",
    "BOS_BEARISH": "Phá vỡ cấu trúc giảm",
    "CHOCH_BULLISH": "Đảo chiều cấu trúc tăng",
    "CHOCH_BEARISH": "Đảo chiều cấu trúc giảm",
    "MSS_BULLISH": "Thay đổi cấu trúc tăng",
    "MSS_BEARISH": "Thay đổi cấu trúc giảm",
    "FAILED_BREAKOUT_BULLISH": "Phá vỡ tăng thất bại",
    "FAILED_BREAKOUT_BEARISH": "Phá vỡ giảm thất bại",
}
_TREND_LABELS = {
    "UPTREND": "Xu hướng tăng",
    "DOWNTREND": "Xu hướng giảm",
    "TRANSITION": "Chuyển tiếp",
    "SIDEWAYS": "Đi ngang",
}
_BIAS_LABELS = {"BULLISH": "nghiêng tăng", "BEARISH": "nghiêng giảm", "NEUTRAL": "trung tính"}
_GENERIC_NEWS_PREFIXES = (
    "chi tiết tin",
    "thông tin công bố",
    "trang chủ",
    "sự kiện",
    "tin tức -",
)


def _is_generic_news(title: str) -> bool:
    lowered = title.casefold().strip()
    return any(lowered.startswith(prefix) for prefix in _GENERIC_NEWS_PREFIXES)


def humanize_text(text: str) -> str:
    """Replace internal engine identifiers with plain Vietnamese terms."""
    cleaned = text or ""
    for pattern, replacement in _GLOSSARY:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = _ISO_DATETIME.sub(lambda match: _date(match.group(1)), cleaned)
    cleaned = _MA_LEVEL.sub(lambda match: f"MA{match.group(1)}", cleaned)
    cleaned = _MA_TEXT_LEVEL.sub(lambda match: match.group(1), cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def humanize_report(report: StockReport) -> StockReport:
    """Normalize every user-visible narrative field once, before persistence."""
    data = report.model_dump()
    for field in (
        "market_overview",
        "volume_analysis",
        "momentum_analysis",
        "trend_market_structure",
        "divergence_analysis",
        "historical_similarities",
        "news_context",
        "final_conclusion",
    ):
        data[field] = humanize_text(data[field])
    for field in ("bullish_evidence", "bearish_evidence", "risks_and_invalidation"):
        data[field] = [humanize_text(item) for item in data[field] if item]
    return StockReport.model_validate(data)


def validate_summary(
    summary: str, supports: list[str], divergence_events: list[dict[str, Any]]
) -> str:
    """Small deterministic validator for claims the prompt cannot guarantee.

    It only repairs wording; facts and statuses remain engine-owned.
    """
    cleaned = summary or ""
    if len(supports) > 1:
        cleaned = re.sub(r"duy nhất", "đáng chú ý", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"không có mức hỗ trợ kỹ thuật nào[^.]*\.",
        "Chưa có tín hiệu hỗ trợ mới cần xác nhận.",
        cleaned,
        flags=re.IGNORECASE,
    )
    latest = divergence_events[-1] if divergence_events else None
    if latest and str(latest.get("kind") or "").lower() == "hidden":
        cleaned = re.sub(
            r"(?:đảo chiều|xác nhận đảo chiều) chắc chắn",
            "tín hiệu cần xác nhận",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned


def _market_zone() -> ZoneInfo:
    return get_settings().market_tz


def _date(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_market_zone()).strftime("%d/%m/%Y")


def _short_date(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_market_zone()).strftime("%d/%m")


def _price(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.0f}".replace(",", ".")


def _decimal(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    formatted = f"{float(value):,.{digits}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _indicator(indicators: dict[str, Any], name: str, **params: Any) -> dict[str, Any]:
    for item in indicators.values():
        if item.get("indicator") != name:
            continue
        item_params = item.get("parameters") or {}
        if all(item_params.get(key) == value for key, value in params.items()):
            return item
    return {}


def _value(item: dict[str, Any], key: str) -> Any:
    return (item.get("values") or {}).get(key)


def _previous(item: dict[str, Any], key: str) -> Any:
    return (item.get("previous_values") or {}).get(key)


def translate_event(event: dict[str, Any] | None) -> str:
    """Render one confirmed structure event as a plain sentence with a date."""
    if not event:
        return ""
    label = _EVENT_LABELS.get(str(event.get("event_type")), "Sự kiện cấu trúc")
    date = _short_date(event.get("broken_at") or event.get("confirmed_at"))
    return f"{label} ngày {date}" if date else label


def _latest_swing(structure: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for swing in reversed(structure.get("swings") or []):
        if swing.get("kind") == kind:
            return swing
    return None


def _last_event(structure: dict[str, Any]) -> dict[str, Any] | None:
    events = structure.get("events") or []
    return events[-1] if events else None


def _structure_row(structure: dict[str, Any]) -> IndicatorAssessment:
    trend = str(structure.get("confirmed_trend") or "SIDEWAYS")
    bias = str(structure.get("break_bias") or "NEUTRAL")
    event = _last_event(structure)
    if trend == "UPTREND":
        status: AssessmentStatus = "positive"
    elif trend == "DOWNTREND":
        status = "negative"
    elif bias == "BULLISH":
        status = "positive"
    elif bias == "BEARISH":
        status = "negative"
    else:
        status = "neutral"
    note = f"{_TREND_LABELS.get(trend, 'Đi ngang')}, {_BIAS_LABELS.get(bias, 'trung tính')}."
    if event:
        note = f"{note} {translate_event(event)}."
    return IndicatorAssessment(
        key="structure",
        label="Cấu trúc thị trường",
        value=f"{_TREND_LABELS.get(trend, 'Đi ngang')} · {_BIAS_LABELS.get(bias, 'trung tính')}",
        status=status,
        note=note,
    )


def _divergence_row(events: list[dict[str, Any]], structure: dict[str, Any]) -> IndicatorAssessment:
    latest = events[-1] if events else None
    if not latest:
        return IndicatorAssessment(
            key="divergence",
            label="Phân kỳ",
            value="Không",
            status="neutral",
            note="Không có phân kỳ đã xác nhận trong cửa sổ phân tích.",
        )
    direction = str(latest.get("direction") or "").lower()
    kind = str(latest.get("kind") or "").lower()
    label = "ẩn" if kind == "hidden" else "thường"
    date = _short_date(latest.get("confirmed_at"))
    trend = str(structure.get("confirmed_trend") or "SIDEWAYS")
    bias = str(structure.get("break_bias") or "NEUTRAL")
    if direction == "bullish":
        status = "positive"
        if trend in {"DOWNTREND", "TRANSITION"} or bias == "BEARISH":
            note = (
                f"Phân kỳ {label} dương xuất hiện khi xu hướng còn yếu"
                " — tín hiệu đảo chiều tiềm năng."
            )
        else:
            note = f"Phân kỳ {label} dương củng cố khả năng hồi phục."
    elif direction == "bearish":
        status = "negative"
        if trend in {"UPTREND", "TRANSITION"} or bias == "BULLISH":
            note = f"Phân kỳ {label} âm xuất hiện khi xu hướng tăng — cảnh báo động lượng suy yếu."
        else:
            note = f"Phân kỳ {label} âm củng cố xu hướng giảm."
    else:
        status, note = "neutral", "Phân kỳ trung tính, chưa đủ cơ sở."
    if date:
        note += f" Xác nhận ngày {date}."
    return IndicatorAssessment(
        key="divergence",
        label="Phân kỳ",
        value="Có — dương"
        if direction == "bullish"
        else "Có — âm"
        if direction == "bearish"
        else "Có",
        status=status,
        note=note,
    )


def build_indicator_rows(
    market: dict[str, Any],
    indicators: dict[str, Any],
    structure: dict[str, Any],
    divergence_events: list[dict[str, Any]],
) -> list[IndicatorAssessment]:
    """Compute one row per indicator family from the confirmed snapshot."""
    close = market.get("close")
    rows: list[IndicatorAssessment] = []

    sma20 = _indicator(indicators, "sma", period=20)
    sma50 = _indicator(indicators, "sma", period=50)
    ma20, ma50 = _value(sma20, "sma"), _value(sma50, "sma")
    if ma20 is not None and ma50 is not None:
        if close is not None and close > ma20 > ma50:
            status, note = "positive", "Giá trên MA20 và MA20 trên MA50."
        elif close is not None and close < ma20 < ma50:
            status, note = "negative", "Giá dưới MA20 và MA20 dưới MA50."
        elif ma20 > ma50:
            status, note = "neutral", "MA20 trên MA50 nhưng giá chưa giữ trên MA20."
        else:
            status, note = "negative", "MA20 dưới MA50, cấu trúc trung bình còn yếu."
        rows.append(
            IndicatorAssessment(
                key="ma",
                label="MA20/50",
                value=f"{_price(ma20)} / {_price(ma50)}",
                status=status,
                note=note,
            )
        )

    macd = _indicator(indicators, "macd")
    histogram = _value(macd, "histogram")
    previous_histogram = _previous(macd, "histogram")
    line = _value(macd, "macd")
    if histogram is not None:
        narrowing = previous_histogram is not None and abs(histogram) < abs(previous_histogram)
        if histogram > 0:
            status = "positive"
            note = "Động lượng dương" + (" nhưng đang thu hẹp." if narrowing else ".")
        elif histogram < 0:
            status = "negative"
            note = "Động lượng âm" + (" nhưng đang thu hẹp." if narrowing else ".")
        else:
            status, note = "neutral", "MACD đi ngang."
        if line is not None and line < 0:
            note = f"MACD còn dưới 0; {note[0].lower() + note[1:]}"
        rows.append(
            IndicatorAssessment(
                key="macd",
                label="MACD",
                value=f"{'+' if histogram > 0 else ''}{_decimal(histogram)}",
                status=status,
                note=note,
            )
        )

    rsi = _indicator(indicators, "rsi")
    rsi_value = _value(rsi, "rsi")
    if rsi_value is not None:
        if rsi_value >= 70:
            status, note = "negative", "Trên 70: quá mua, rủi ro điều chỉnh."
        elif rsi_value <= 30:
            status, note = "negative", "Dưới 30: quá bán, động lượng yếu."
        else:
            status, note = "neutral", "Trong vùng 30–70: trung tính, chưa quá mua."
        rows.append(
            IndicatorAssessment(
                key="rsi", label="RSI(14)", value=_decimal(rsi_value), status=status, note=note
            )
        )

    adx = _indicator(indicators, "adx")
    plus_di = _value(_indicator(indicators, "plus_di"), "plus_di")
    minus_di = _value(_indicator(indicators, "minus_di"), "minus_di")
    adx_value = _value(adx, "adx")
    if adx_value is not None:
        if adx_value < 25:
            direction = (
                "+DI > -DI, nghiêng tăng"
                if plus_di is not None and minus_di is not None and plus_di > minus_di
                else "-DI > +DI, nghiêng giảm"
                if plus_di is not None and minus_di is not None and minus_di > plus_di
                else "hai đường DI chưa phân định"
            )
            status = "neutral"
            note = f"ADX {_decimal(adx_value)} dưới 25: chưa có xu hướng rõ; {direction}."
        elif plus_di is not None and minus_di is not None and plus_di > minus_di:
            status, note = "positive", "ADX trên 25 và +DI > -DI: xu hướng tăng."
        elif plus_di is not None and minus_di is not None:
            status, note = "negative", "ADX trên 25 và -DI > +DI: xu hướng giảm."
        else:
            status, note = "neutral", f"ADX {_decimal(adx_value)}."
        rows.append(
            IndicatorAssessment(
                key="adx", label="ADX/DI", value=_decimal(adx_value), status=status, note=note
            )
        )

    ichimoku = _indicator(indicators, "ichimoku")
    senkou_a = _value(ichimoku, "cloud_a") or _value(ichimoku, "senkou_a")
    senkou_b = _value(ichimoku, "cloud_b") or _value(ichimoku, "senkou_b")
    if senkou_a is not None and senkou_b is not None and close is not None:
        cloud_top, cloud_bottom = max(senkou_a, senkou_b), min(senkou_a, senkou_b)
        if close > cloud_top:
            status, value, note = "positive", "Trên mây", "Giá trên mây — xu hướng tăng."
        elif close < cloud_bottom:
            status, value, note = "negative", "Dưới mây", "Giá dưới mây — xu hướng giảm."
        else:
            status, value, note = "neutral", "Trong mây", "Giá trong mây — chưa rõ xu hướng."
        rows.append(
            IndicatorAssessment(
                key="ichimoku",
                label="Ichimoku",
                value=value,
                status=status,
                note=note,
            )
        )

    bollinger = _indicator(indicators, "bollinger")
    percent_b = _value(bollinger, "percent_b")
    middle, lower, upper = (
        _value(bollinger, "middle"),
        _value(bollinger, "lower"),
        _value(bollinger, "upper"),
    )
    if percent_b is not None:
        if percent_b <= 0.2:
            status, note = "negative", "Giá sát dải dưới, động lượng yếu."
        elif percent_b >= 0.8:
            status, note = "positive", "Giá sát dải trên, động lượng mạnh nhưng dễ điều chỉnh."
        else:
            status, note = "neutral", "Giá ở vùng giữa hai dải."
        rows.append(
            IndicatorAssessment(
                key="bollinger",
                label="Bollinger",
                value=f"%B {_decimal(percent_b, 2)}",
                status=status,
                note=note,
            )
        )
    del middle, lower, upper

    rvol = _indicator(indicators, "rvol")
    rvol_value = _value(rvol, "rvol")
    if rvol_value is not None:
        if rvol_value >= 1.5:
            status, note = "positive", "Khối lượng đột biến — tín hiệu tốt, có xác nhận dòng tiền."
        elif rvol_value < 0.8:
            status, note = "negative", "Khối lượng yếu — thiếu xác nhận dòng tiền."
        else:
            status, note = "neutral", "Khối lượng bình thường."
        rows.append(
            IndicatorAssessment(
                key="volume",
                label="Khối lượng",
                value=f"{_decimal(rvol_value, 2)}x TB20",
                status=status,
                note=note,
            )
        )

    obv = _indicator(indicators, "obv")
    obv_value, previous_obv = _value(obv, "obv"), _previous(obv, "obv")
    if obv_value is not None:
        if previous_obv is not None and obv_value > previous_obv:
            status, value, note = "positive", "Tăng", "OBV tăng, dòng tiền đang vào."
        elif previous_obv is not None and obv_value < previous_obv:
            status, value, note = "negative", "Giảm", "OBV giảm, dòng tiền đang ra."
        else:
            status, value, note = "neutral", "Không đổi", "OBV không đổi, chưa xác nhận dòng tiền."
        rows.append(
            IndicatorAssessment(
                key="obv", label="OBV (dòng tiền)", value=value, status=status, note=note
            )
        )

    rows.append(_structure_row(structure))
    rows.append(_divergence_row(divergence_events, structure))
    return enrich_rows(
        rows,
        AssessmentContext(
            market=market,
            indicators=indicators,
            structure=structure,
            divergence=divergence_events,
        ),
    )


def _stance(structure: dict[str, Any]) -> tuple[str, str]:
    trend = str(structure.get("confirmed_trend") or "SIDEWAYS")
    bias = str(structure.get("break_bias") or "NEUTRAL")
    if trend == "UPTREND":
        return "bullish", "Xu hướng tăng"
    if trend == "DOWNTREND":
        return "bearish", "Xu hướng giảm"
    if trend == "TRANSITION" and bias == "BEARISH":
        return "mixed", "Nghiêng giảm"
    if trend == "TRANSITION" and bias == "BULLISH":
        return "mixed", "Nghiêng tăng"
    return "neutral", "Đi ngang"


def build_report_card(state: dict[str, Any], report: StockReport) -> AnalysisReportCard:
    """Build the deterministic JSON card displayed beside the narrative."""
    technical = state.get("technical_summary") or {}
    symbols = list(state.get("symbols") or [state.get("symbol") or ""])
    symbols = [symbol for symbol in symbols if symbol]
    timeframe = str(state.get("timeframe") or "1D")
    first = technical.get(symbols[0], {}) if symbols else {}
    market = first.get("market") or {}
    structure = first.get("structure") or {}
    divergence_events = first.get("divergence") or []
    rows: list[IndicatorAssessment] = []
    for symbol in symbols:
        item = technical.get(symbol) or {}
        symbol_rows = build_indicator_rows(
            item.get("market") or {},
            item.get("indicators") or {},
            item.get("structure") or {},
            item.get("divergence") or [],
        )
        if len(symbols) > 1:
            for row in symbol_rows:
                row = row.model_copy(update={"label": f"{symbol} · {row.label}"})
                rows.append(row)
        else:
            rows.extend(symbol_rows)

    supports = list(report.bullish_evidence)
    risks = list(dict.fromkeys([*report.bearish_evidence, *report.risks_and_invalidation]))
    if not supports:
        supports = [row.note for row in rows if row.status == "positive"][:4]
    if not risks:
        risks = [row.note for row in rows if row.status == "negative"][:4]
    stance, stance_label = _stance(structure)

    close = market.get("close")
    previous_close = market.get("previous_close")
    change_pct = None
    if close is not None and previous_close:
        change_pct = round((close / previous_close - 1) * 100, 2)

    watch_levels: list[WatchLevel] = []
    low = _latest_swing(structure, "LOW")
    high = _latest_swing(structure, "HIGH")
    if low and low.get("price") is not None:
        watch_levels.append(
            WatchLevel(label="Hỗ trợ gần", price=low["price"], note="Đáy gần nhất đã xác nhận.")
        )
    if high and high.get("price") is not None:
        watch_levels.append(
            WatchLevel(label="Kháng cự gần", price=high["price"], note="Đỉnh gần nhất đã xác nhận.")
        )
    ma20 = _value(_indicator(first.get("indicators") or {}, "sma", period=20), "sma")
    if ma20 is not None:
        watch_levels.append(
            WatchLevel(label="MA20", price=ma20, note="Mốc cân bằng ngắn hạn cần theo dõi.")
        )
    bollinger = _indicator(first.get("indicators") or {}, "bollinger")
    for key, label, note in (
        ("lower", "Dải dưới Bollinger", "Vùng hỗ trợ động."),
        ("middle", "Dải giữa Bollinger", "Vùng cân bằng."),
        ("upper", "Dải trên Bollinger", "Vùng kháng cự động."),
    ):
        value = _value(bollinger, key)
        if value is not None:
            watch_levels.append(WatchLevel(label=label, price=value, note=note))
    deduped_levels: list[WatchLevel] = []
    for level in watch_levels:
        if any(abs(level.price - existing.price) < 1e-6 for existing in deduped_levels):
            continue
        deduped_levels.append(level)
    watch_levels = deduped_levels

    market_status = str(market.get("market_status") or "")
    as_of = _date(market.get("data_timestamp") or state.get("analyzed_at"))
    freshness = market.get("freshness") or state.get("data_freshness") or {}
    freshness_status = str(freshness.get("status") or "unknown")
    warnings: list[str] = []
    if freshness_status == "stale":
        expected = _date(freshness.get("expected_session"))
        latest = _date(freshness.get("latest_session")) or as_of
        sessions = freshness.get("sessions_behind")
        data_note = (
            f"Dữ liệu giá mới nhất {latest}, trễ {sessions} phiên so với phiên kỳ vọng "
            f"{expected}. Không dùng dữ liệu này để khẳng định 'hiện tại'."
        )
        warnings.append("data_stale")
    elif market_status == "LIVE":
        data_note = f"Dữ liệu đã đóng đến {as_of}, có giá trong phiên tham khảo."
    elif market_status == "HISTORICAL":
        data_note = f"Phân tích theo dữ liệu lịch sử đến {as_of}."
    else:
        data_note = f"Chỉ dùng nến đã đóng đến {as_of}; chưa có giá trong phiên."
    if freshness_status == "unknown" and timeframe == "1D":
        warnings.append("data_freshness_unknown")

    news_items: list[NewsItem] = []
    seen_urls: set[str] = set()
    for row in state.get("news_evidence") or []:
        url = row.get("canonical_url") or row.get("source_url")
        title = str(row.get("title") or "")
        if not url or url in seen_urls or _is_generic_news(title):
            continue
        seen_urls.add(url)
        news_items.append(
            NewsItem(
                title=title or "Bài viết",
                publisher=row.get("publisher") or "",
                published_at=_date(row.get("published_at")),
                url=url,
                source_type=row.get("source_type") or "news",
                scope="market" if row.get("associated_symbol") == "MARKET" else "company",
            )
        )

    return AnalysisReportCard(
        symbols=symbols,
        timeframe=timeframe,
        as_of=as_of,
        price=close,
        change_pct=change_pct,
        stance=stance,
        stance_label=stance_label,
        summary=validate_summary(report.final_conclusion, supports, divergence_events),
        indicators=rows,
        supports=supports,
        risks=risks,
        watch_levels=watch_levels,
        history=report.historical_similarities or "Chưa có case tương tự.",
        news=report.news_context or "Chưa có tin tức phù hợp.",
        news_items=news_items[:8],
        data_note=data_note,
        data_freshness=freshness or None,
        warnings=warnings,
    )


def _friendly_finding(finding: dict[str, Any] | None) -> dict[str, Any]:
    if not finding:
        return {}
    return {
        "direction": str(finding.get("direction") or "neutral"),
        "strength": str(finding.get("strength") or "unknown"),
        "observations": [humanize_text(item) for item in finding.get("observations") or []],
    }


def prompt_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strip engine identifiers and raw structure arrays before the LLM call."""
    technical = evidence.get("technical_summary") or {}
    market = evidence.get("market") or {}
    friendly_indicators = {}
    for symbol, item in technical.items():
        rows = []
        for row in build_indicator_rows(
            item.get("market") or {},
            item.get("indicators") or {},
            item.get("structure") or {},
            item.get("divergence") or [],
        ):
            payload = row.model_dump()
            if row.key == "ma":
                # MA values are visible on the chart; keep only the relation and
                # reserve the numeric level for the watch-level section.
                payload["value"] = None
            rows.append(payload)
        friendly_indicators[symbol] = rows
    trend = evidence.get("trend") or {}
    structure = (trend.get("evidence") or {}).get("structure") or {}
    friendly_trend = {
        **_friendly_finding(trend),
        "trend": str(structure.get("confirmed_trend") or "SIDEWAYS"),
        "bias": str(structure.get("break_bias") or "NEUTRAL"),
        "latest_event": (
            f"{_last_event(structure).get('event_type')} "
            f"{_short_date(_last_event(structure).get('broken_at') or _last_event(structure).get('confirmed_at'))}"
            if _last_event(structure)
            else ""
        ),
    }
    divergence = evidence.get("divergence") or {}
    latest_divergence = (divergence.get("evidence") or {}).get("latest") or {}
    friendly_divergence = {
        "direction": str(divergence.get("direction") or "neutral"),
        "kind": latest_divergence.get("kind"),
        "confirmed_at": _short_date(latest_divergence.get("confirmed_at")),
        "price_confirmed_at": _short_date(latest_divergence.get("price_confirmed_at")),
        "observations": [humanize_text(item) for item in divergence.get("observations") or []],
    }
    history = [
        {
            "symbol": row.get("symbol"),
            "analyzed_at": _date(row.get("analyzed_at")),
            "thesis": row.get("user_thesis"),
            "outcome": row.get("historical_outcome"),
        }
        for row in evidence.get("historical_evidence") or []
    ]
    news = [
        {
            "symbol": row.get("symbol"),
            "title": row.get("title"),
            "published_at": _date(row.get("published_at")),
            "source": row.get("source") or row.get("source_type"),
        }
        for row in evidence.get("news_evidence") or []
    ]
    return {
        "question": evidence.get("question"),
        "symbols": list(technical) or [market.get("symbol")],
        "timeframe": market.get("timeframe"),
        "as_of": _date(market.get("data_timestamp")),
        "price": {
            "close": market.get("close"),
            "previous_close": market.get("previous_close"),
            "volume": market.get("volume"),
            "previous_volume": market.get("previous_volume"),
        },
        "indicators": friendly_indicators,
        "volume": _friendly_finding(evidence.get("volume")),
        "momentum": _friendly_finding(evidence.get("momentum")),
        "trend": friendly_trend,
        "divergence": friendly_divergence,
        "data_freshness": market.get("freshness") or evidence.get("data_freshness") or {},
        "historical_evidence": history,
        "news_evidence": news,
    }


def prompt_glossary() -> str:
    """Keep original structure/divergence symbols; explain only when needed."""
    return (
        "Giữ nguyên symbol kỹ thuật: BOS_BULLISH/BOS_BEARISH, CHOCH_*, MSS_*, "
        "TRANSITION/UPTREND/DOWNTREND, hidden bullish/bearish, regular bullish/bearish, "
        "HH/HL/LH/LL. Chỉ thêm giải thích ngắn nếu cần, không dịch thay symbol."
    )
