"""Assessment semantics and freshness must stay explicit and non-breaking."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.agents.report import build_indicator_rows, build_report_card, prompt_evidence
from app.schemas.agent import StockReport
from app.services.market_freshness import (
    expected_session_date,
    weekday_sessions_between,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MARKET = {
    "symbol": "VHM",
    "timeframe": "1D",
    "data_timestamp": "2026-11-19T17:00:00+00:00",
    "market_status": "CLOSED_ONLY",
    "close": 60_400,
    "previous_close": 60_400,
    "high": 60_400,
    "low": 58_900,
    "volume": 6_228_300,
    "previous_volume": 3_513_900,
    "freshness": {
        "status": "stale",
        "latest_session": "2026-11-20",
        "expected_session": "2026-11-23",
        "sessions_behind": 1,
    },
}
INDICATORS = {
    "sma20": {"indicator": "sma", "parameters": {"period": 20}, "values": {"sma": 62_185}},
    "sma50": {"indicator": "sma", "parameters": {"period": 50}, "values": {"sma": 64_776}},
    "rsi14": {"indicator": "rsi", "parameters": {"period": 14}, "values": {"rsi": 39.5}},
    "macd": {
        "indicator": "macd",
        "values": {"macd": -1_005, "signal_line": -1_019, "histogram": 13.6},
        "previous_values": {"macd": -966, "signal_line": -1_022, "histogram": 56.1},
    },
    "adx14": {"indicator": "adx", "values": {"adx": 21.7}},
    "plus_di": {"indicator": "plus_di", "values": {"plus_di": 16.9}},
    "minus_di": {"indicator": "minus_di", "values": {"minus_di": 31.7}},
    "ichimoku": {
        "indicator": "ichimoku",
        "values": {"tenkan": 61_350, "kijun": 61_450, "senkou_a": 61_400, "senkou_b": 65_900},
    },
    "bollinger": {
        "indicator": "bollinger",
        "values": {"upper": 64_250, "middle": 62_185, "lower": 60_120, "percent_b": 0.068},
    },
    "rvol": {"indicator": "rvol", "values": {"rvol": 1.14}},
    "obv": {
        "indicator": "obv",
        "values": {"obv": 340_772_700},
        "previous_values": {"obv": 340_772_700},
    },
}
STRUCTURE = {
    "confirmed_trend": "TRANSITION",
    "break_bias": "BEARISH",
    "swings": [{"kind": "LOW", "price": 60_400}, {"kind": "HIGH", "price": 64_900}],
    "events": [
        {
            "event_type": "BOS_BEARISH",
            "broken_at": "2026-10-11T17:00:00+00:00",
            "confirmed_at": "2026-10-01T17:00:00+00:00",
        }
    ],
}
DIVERGENCE = [
    {
        "direction": "bullish",
        "kind": "hidden",
        "confirmed_at": "2026-11-18T17:00:00+00:00",
        "price_confirmed_at": None,
        "oscillators": [{"indicator": "RSI"}],
    }
]
REPORT = StockReport(
    market_overview="x",
    volume_analysis="x",
    momentum_analysis="x",
    trend_market_structure="x",
    divergence_analysis="x",
    final_conclusion="Cấu trúc chuyển tiếp, nghiêng giảm.",
)


def rows():
    return {
        row.key: row
        for row in build_indicator_rows(
            MARKET,
            INDICATORS,
            STRUCTURE,
            DIVERGENCE,
        )
    }


def test_expected_session_before_market_close_is_previous_weekday():
    analyzed = datetime(2026, 11, 24, 0, 22, tzinfo=TZ)
    assert expected_session_date(analyzed, TZ, time(15, 0)) == date(2026, 11, 23)
    assert weekday_sessions_between(date(2026, 11, 20), date(2026, 11, 23)) == 1


def test_macd_below_zero_is_neutral_not_positive():
    row = rows()["macd"]
    assert row.status == "neutral"
    assert row.state["zero_zone"] == "negative"
    assert row.state["signal_relation"] == "above"
    assert row.state["momentum_state"] == "bullish_weakening"
    assert row.conflicts


def test_hidden_bullish_divergence_is_not_a_confirmed_reversal():
    row = rows()["divergence"]
    assert row.status == "neutral"
    assert row.state["divergence_type"] == "hidden_bullish"
    assert "tích lũy" in row.note
    assert row.value == "hidden bullish"
    assert row.conflicts
    assert row.signal_phase == "indicator_confirmed"
    assert row.state["post_signal_price_confirmation"] is False


def test_divergence_becomes_price_confirmed_only_after_later_structure_break():
    structure = {
        **STRUCTURE,
        "events": [
            *STRUCTURE["events"],
            {"event_type": "BOS_BULLISH", "broken_at": "2026-11-22T17:00:00+00:00"},
        ],
    }
    row = {
        item.key: item for item in build_indicator_rows(MARKET, INDICATORS, structure, DIVERGENCE)
    }["divergence"]
    assert row.signal_phase == "price_confirmed"
    assert row.confirmation_level == "price"
    assert row.state["post_signal_price_confirmation"] is True


def test_transition_structure_is_neutral_with_bias():
    row = rows()["structure"]
    assert row.status == "neutral"
    assert row.state == {"trend_state": "transition", "trend_bias": "bearish"}
    assert "TRANSITION/BEARISH" in row.note
    assert "BOS_BEARISH" in row.note


def test_volume_and_bollinger_do_not_force_direction():
    data = rows()
    assert data["volume"].status == "neutral"
    assert data["volume"].state["volume_state"] == "normal"
    assert data["bollinger"].status == "neutral"
    assert data["bollinger"].state["band_walk"] == "lower"


def test_prompt_evidence_uses_market_dates_only():
    evidence = {
        "question": "VHM",
        "market": MARKET,
        "technical_summary": {
            "VHM": {
                "market": MARKET,
                "indicators": INDICATORS,
                "structure": STRUCTURE,
                "divergence": DIVERGENCE,
            }
        },
        "trend": {"direction": "bearish", "evidence": {"structure": STRUCTURE}},
        "divergence": {
            "direction": "bullish",
            "observations": ["Phân kỳ đã xác nhận tại 2026-11-18T17:00:00+00:00."],
            "evidence": {"latest": DIVERGENCE[0]},
        },
    }
    payload = json.dumps(prompt_evidence(evidence), ensure_ascii=False)
    assert "2026-11-18T17:00" not in payload
    assert "19/11" in payload


def test_report_card_surfaces_stale_warning_and_generic_news_filter():
    state = {
        "symbol": "VHM",
        "symbols": ["VHM"],
        "timeframe": "1D",
        "analyzed_at": "2026-11-23T17:22:00+00:00",
        "technical_summary": {"VHM": {"market": MARKET}},
        "news_evidence": [
            {
                "title": "Chi tiết tin - Ủy ban Chứng khoán Nhà nước",
                "canonical_url": "https://a.vn/x",
            },
            {
                "title": "Vinhomes phát hành trái phiếu",
                "canonical_url": "https://b.vn/article",
                "publisher": "b.vn",
                "published_at": "2026-11-21T03:00:00+00:00",
                "associated_symbol": "VHM",
            },
        ],
    }
    card = build_report_card(state, REPORT)
    assert "data_stale" in card.warnings
    assert "trễ 1 phiên" in card.data_note
    assert [item.title for item in card.news_items] == ["Vinhomes phát hành trái phiếu"]
