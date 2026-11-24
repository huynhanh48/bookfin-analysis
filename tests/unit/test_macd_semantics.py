"""MACD signal/histogram wording must match the numeric definition."""

from app.agents.report import build_indicator_rows, validate_summary
from app.indicators.factory import interpret_signal

VALUES = {"macd": -1005.0, "signal_line": -1019.0, "histogram": 14.0}


def test_histogram_sign_equals_macd_minus_signal():
    assert VALUES["histogram"] == VALUES["macd"] - VALUES["signal_line"]


def test_interpret_signal_labels_above_and_below_signal():
    assert interpret_signal("macd", VALUES, {"histogram": 10.0}, 60_400) == "above_signal"
    below = {"macd": -1200.0, "signal_line": -1100.0, "histogram": -100.0}
    assert interpret_signal("macd", below, {"histogram": -90.0}, 60_400) == "below_signal"


def test_interpret_signal_detects_histogram_crosses():
    assert interpret_signal("macd", VALUES, {"histogram": -5.0}, 60_400) == "bullish_cross"
    below = {"macd": -1200.0, "signal_line": -1100.0, "histogram": -100.0}
    assert interpret_signal("macd", below, {"histogram": 5.0}, 60_400) == "bearish_cross"


def test_macd_below_zero_is_neutral_not_checked():
    rows = build_indicator_rows(
        {
            "close": 60_400,
            "previous_close": 60_400,
            "volume": 6_000_000,
            "previous_volume": 3_000_000,
        },
        {
            "macd": {
                "indicator": "macd",
                "values": {"macd": -1005, "signal_line": -1019, "histogram": 14},
                "previous_values": {"macd": -966, "signal_line": -1022, "histogram": 56},
            }
        },
        {"confirmed_trend": "TRANSITION", "break_bias": "BEARISH", "events": []},
        [],
    )
    macd = next(row for row in rows if row.key == "macd")
    assert macd.status == "neutral"
    assert macd.state["zero_zone"] == "negative"
    assert macd.state["momentum_state"] == "bullish_weakening"
    assert "dưới 0" in macd.note


def test_narrative_validator_removes_unsupported_absolutes():
    text = "Điểm sáng duy nhất là phân kỳ; đảo chiều chắc chắn."
    assert "duy nhất" not in validate_summary(text, ["a", "b"], [])
    assert "đảo chiều chắc chắn" not in validate_summary(
        text, ["a"], [{"kind": "hidden", "direction": "bullish"}]
    )
    assert "Không có mức hỗ trợ" not in validate_summary(
        "Không có mức hỗ trợ kỹ thuật nào được xác nhận.", ["a"], []
    )
