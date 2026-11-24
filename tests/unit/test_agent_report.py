"""The chat report card is deterministic, friendly and free of engine keys."""

import json

from app.agents.report import (
    build_report_card,
    humanize_report,
    humanize_text,
    prompt_evidence,
    translate_event,
)
from app.agents.research import render_markdown
from app.schemas.agent import StockReport

MARKET = {
    "symbol": "VHM",
    "timeframe": "1D",
    "data_timestamp": "2026-11-19T17:00:00+00:00",
    "market_status": "CLOSED_ONLY",
    "close": 60_400,
    "previous_close": 60_400,
    "volume": 4_000_000,
    "previous_volume": 2_260_000,
    "realtime_price": None,
}
INDICATORS = {
    "sma20": {"indicator": "sma", "parameters": {"period": 20}, "values": {"sma": 62_180}},
    "sma50": {"indicator": "sma", "parameters": {"period": 50}, "values": {"sma": 64_780}},
    "rsi14": {"indicator": "rsi", "parameters": {"period": 14}, "values": {"rsi": 39.53}},
    "macd": {
        "indicator": "macd",
        "values": {"macd": -1_005, "histogram": 13.58},
        "previous_values": {"histogram": 20.0},
    },
    "adx14": {"indicator": "adx", "values": {"adx": 21.67}},
    "plus_di": {"indicator": "plus_di", "values": {"plus_di": 16.87}},
    "minus_di": {"indicator": "minus_di", "values": {"minus_di": 31.74}},
    "ichimoku": {
        "indicator": "ichimoku",
        "values": {
            "tenkan": 61_350,
            "kijun": 61_450,
            "senkou_a": 61_400,
            "senkou_b": 65_900,
        },
    },
    "bollinger": {
        "indicator": "bollinger",
        "values": {
            "upper": 64_250,
            "middle": 62_180,
            "lower": 60_120,
            "percent_b": 0.06784,
        },
    },
    "rvol": {"indicator": "rvol", "values": {"rvol": 1.14}},
    "obv": {
        "indicator": "obv",
        "values": {"obv": 340_800_000},
        "previous_values": {"obv": 340_800_000},
    },
}
STRUCTURE = {
    "confirmed_trend": "TRANSITION",
    "break_bias": "BEARISH",
    "swings": [
        {"kind": "LOW", "price": 60_100},
        {"kind": "HIGH", "price": 66_000},
        {"kind": "LOW", "price": 60_400},
    ],
    "events": [
        {
            "event_type": "BOS_BEARISH",
            "broken_at": "2026-11-15T17:00:00+00:00",
            "confirmed_at": "2026-11-14T17:00:00+00:00",
        }
    ],
}
DIVERGENCE = [
    {
        "direction": "bullish",
        "kind": "hidden",
        "confirmed_at": "2026-11-17T17:00:00+00:00",
    }
]
STATE = {
    "symbol": "VHM",
    "symbols": ["VHM"],
    "timeframe": "1D",
    "analyzed_at": "2026-11-23T17:00:59+00:00",
    "technical_summary": {
        "VHM": {
            "market": MARKET,
            "indicators": INDICATORS,
            "structure": STRUCTURE,
            "divergence": DIVERGENCE,
        }
    },
}
REPORT = StockReport(
    market_overview="Giá đang ở vùng chuyển tiếp.",
    volume_analysis="Khối lượng bình thường.",
    momentum_analysis="Động lượng còn yếu.",
    trend_market_structure="Cấu trúc nghiêng giảm.",
    divergence_analysis="Có phân kỳ ẩn tăng.",
    bullish_evidence=["Phân kỳ ẩn tăng đã xác nhận."],
    bearish_evidence=["Giá dưới MA20 và MA50."],
    risks_and_invalidation=["Mất hỗ trợ 60.400 sẽ củng cố xu hướng giảm."],
    final_conclusion="Cấu trúc TRANSITION, break_bias BEARISH; BOS_BEARISH 15/11.",
)


def test_report_card_computes_statuses_and_formats_values():
    card = build_report_card(STATE, humanize_report(REPORT))
    rows = {row.key: row for row in card.indicators}

    assert card.symbols == ["VHM"]
    assert card.as_of == "20/11/2026"
    assert card.price == 60_400
    assert card.change_pct == 0.0
    assert card.stance == "mixed"
    assert card.stance_label == "Nghiêng giảm"
    assert rows["ma"].status == "negative"
    assert rows["ma"].value == "62.180 / 64.780"
    assert rows["rsi"].status == "neutral"
    assert "30–70" in rows["rsi"].note
    assert rows["macd"].status == "positive"
    assert rows["adx"].status == "neutral"
    assert "dưới 25" in rows["adx"].note
    assert rows["ichimoku"].status == "negative"
    assert rows["ichimoku"].value == "Dưới mây"
    assert rows["bollinger"].status == "negative"
    assert rows["volume"].status == "neutral"
    assert rows["volume"].label == "Khối lượng"
    assert rows["volume"].value == "1,14x TB20"
    assert rows["obv"].status == "neutral"
    assert rows["obv"].value == "Không đổi"
    assert rows["obv"].label == "OBV (dòng tiền)"
    assert rows["structure"].status == "negative"
    assert rows["divergence"].status == "positive"
    assert rows["divergence"].value == "Có — dương"
    assert "đảo chiều tiềm năng" in rows["divergence"].note
    assert "phá vỡ cấu trúc giảm" in rows["structure"].note.lower()


def test_humanize_removes_internal_identifiers():
    text = humanize_text("CLOSED_ONLY, break_bias BEARISH, BOS_BEARISH 15/11, HH 246.500, null")
    assert "CLOSED_ONLY" not in text
    assert "break_bias" not in text
    assert "BOS_BEARISH" not in text
    assert "HH" not in text
    assert "null" not in text
    assert "chỉ dùng nến đã đóng" in text
    assert "phá vỡ cấu trúc giảm" in text
    assert "đỉnh cao hơn" in text


def test_translate_event_is_plain_and_dated():
    assert translate_event(STRUCTURE["events"][0]) == "Phá vỡ cấu trúc giảm ngày 16/11"


def test_markdown_table_has_status_icons_without_raw_keys():
    report = humanize_report(REPORT)
    card = build_report_card(STATE, report)
    markdown = render_markdown({**STATE, "report_card": card.model_dump()}, report)
    assert "| Chỉ báo | Đánh giá | Ghi chú |" in markdown
    assert "| Chỉ báo | Giá trị |" not in markdown
    assert "✓" in markdown and "✕" in markdown
    assert "sma=" not in markdown
    assert "CLOSED_ONLY" not in markdown
    assert "break_bias" not in markdown
    assert "BOS_BEARISH" not in markdown


def test_report_card_includes_news_sources_with_scope():
    state = {
        **STATE,
        "news_evidence": [
            {
                "title": "VHM lãi kỷ lục",
                "publisher": "cafef.vn",
                "published_at": "2026-11-20T03:00:00+00:00",
                "canonical_url": "https://cafef.vn/vhm",
                "source_url": "https://cafef.vn/vhm",
                "source_type": "news",
                "associated_symbol": "VHM",
            },
            {
                "title": "Dòng tiền thị trường",
                "publisher": "vietstock.vn",
                "published_at": "2026-11-22T03:00:00+00:00",
                "canonical_url": "https://vietstock.vn/market",
                "source_url": "https://vietstock.vn/market",
                "source_type": "news",
                "associated_symbol": "MARKET",
            },
        ],
    }
    card = build_report_card(state, humanize_report(REPORT))
    assert [item.scope for item in card.news_items] == ["company", "market"]
    assert card.news_items[0].published_at == "20/11/2026"
    assert card.news_items[1].url == "https://vietstock.vn/market"


def test_prompt_evidence_strips_engine_structures():
    evidence = {
        "question": "VHM nhận định",
        "market": MARKET,
        "technical_summary": STATE["technical_summary"],
        "trend": {"direction": "bearish", "evidence": {"structure": STRUCTURE}},
        "volume": {"direction": "neutral"},
        "momentum": {"direction": "neutral"},
        "divergence": {"direction": "bullish"},
        "historical_evidence": [],
        "news_evidence": [],
    }
    payload = json.dumps(prompt_evidence(evidence), ensure_ascii=False)
    assert "CLOSED_ONLY" not in payload
    assert "break_bias" not in payload
    assert "swings" not in payload
    assert "events" not in payload
    assert "Phá vỡ cấu trúc giảm" in payload
    assert "62.180 / 64.780" in payload
