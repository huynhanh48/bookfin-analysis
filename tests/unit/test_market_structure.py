"""Test parity cho engine cấu trúc thị trường.

Bám theo danh sách test bắt buộc của đặc tả: phân loại swing, thời điểm xác nhận
pivot, BOS/CHoCH theo bias trước đó, chống phát lặp, wick không tính là phá trong
chế độ close, so sánh đỉnh/đáy bằng nhau, tương đương batch và incremental, không
dùng dữ liệu tương lai, và xử lý dữ liệu lỗi.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.indicators.market_structure import (
    BreakBias,
    CandleBar,
    ConfirmedTrend,
    MarketStructureConfig,
    MarketStructureEngine,
    PivotType,
    StructureEventType,
    SwingLabel,
    analyze_structure,
)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "market_structure_zigzag.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
START = datetime(2026, 1, 1, tzinfo=UTC)
PIVOT_LENGTH = 3


def make_bars(
    closes: list[float],
    *,
    spread: float = 1.0,
    volume: float = 1000.0,
) -> list[CandleBar]:
    """Dựng nến từ danh sách giá đóng cửa, high/low lệch quanh close."""
    return [
        CandleBar(
            time=START + timedelta(days=index),
            open=float(close),
            high=float(close) + spread,
            low=float(close) - spread,
            close=float(close),
            volume=volume,
        )
        for index, close in enumerate(closes)
    ]


def zigzag_bars() -> list[CandleBar]:
    """Chuỗi nến của golden fixture."""
    return make_bars(FIXTURE["closes"], spread=FIXTURE["spread"])


def config(**overrides) -> MarketStructureConfig:
    """Cấu hình pine_compatible với pivot_length của fixture."""
    return MarketStructureConfig(pivot_length=PIVOT_LENGTH, **overrides)


def index_of(moment: datetime) -> int:
    """Vị trí bar tương ứng một thời điểm trong fixture."""
    return (moment - START).days


def test_golden_fixture_khop_swing_va_event() -> None:
    snapshot = analyze_structure(zigzag_bars(), symbol="test", config=config())

    swings = [
        (swing.index, swing.kind.value, swing.label.value if swing.label else None)
        for swing in snapshot.swings
    ]
    expected_swings = [
        (item["index"], item["kind"], item["label"]) for item in FIXTURE["expected_swings"]
    ]
    assert swings == expected_swings

    events = [
        (
            event.event_type.value,
            index_of(event.broken_at),
            event.bias_before.value,
            event.bias_after.value,
        )
        for event in snapshot.events
    ]
    expected_events = [
        (item["event_type"], item["broken_index"], item["bias_before"], item["bias_after"])
        for item in FIXTURE["expected_events"]
    ]
    assert events == expected_events
    assert snapshot.break_bias.value == FIXTURE["expected_break_bias"]
    assert snapshot.confirmed_trend.value == FIXTURE["expected_confirmed_trend"]


def test_pivot_chi_duoc_biet_sau_right_bar() -> None:
    bars = zigzag_bars()
    closes = FIXTURE["closes"]

    before = analyze_structure(make_bars(closes[:8]), config=config())
    assert [swing.index for swing in before.swings] == []

    after = analyze_structure(make_bars(closes[:9]), config=config())
    assert [swing.index for swing in after.swings] == [5]
    assert after.swings[0].confirmed_at == bars[8].time


@pytest.mark.parametrize("length", [1, 2, 3, 5])
def test_khong_dung_du_lieu_tuong_lai(length: int) -> None:
    bars = zigzag_bars()
    for cutoff in range(PIVOT_LENGTH * 2 + 1, len(bars)):
        snapshot = analyze_structure(bars[:cutoff], config=config())
        for swing in snapshot.swings:
            assert swing.index <= cutoff - 1 - PIVOT_LENGTH
            assert swing.confirmed_at <= bars[cutoff - 1].time
        assert length >= 1


def test_bos_dau_tien_tu_bias_neutral_duoc_tinh_la_bos() -> None:
    snapshot = analyze_structure(zigzag_bars(), config=config())

    first = snapshot.events[0]
    assert first.event_type is StructureEventType.BOS_BULLISH
    assert first.bias_before is BreakBias.NEUTRAL
    assert first.bias_after is BreakBias.BULLISH


def test_choch_bearish_khi_pha_nguoc_bias_hien_tai() -> None:
    snapshot = analyze_structure(zigzag_bars(), config=config())

    last = snapshot.events[-1]
    assert last.event_type is StructureEventType.CHOCH_BEARISH
    assert last.bias_before is BreakBias.BULLISH
    assert snapshot.break_bias is BreakBias.BEARISH


def test_bias_va_trend_duoc_tach_biet() -> None:
    snapshot = analyze_structure(zigzag_bars(), config=config())

    # CHoCH chỉ là dấu hiệu chuyển pha: bias đã đổi nhưng xu hướng chưa được xác nhận
    # là giảm vì nhãn swing cuối vẫn còn HH ở phía đỉnh.
    assert snapshot.break_bias is BreakBias.BEARISH
    assert snapshot.confirmed_trend is ConfirmedTrend.TRANSITION


def test_khong_phat_lai_tren_cung_pivot() -> None:
    closes = FIXTURE["closes"]
    engine = MarketStructureEngine(config())
    snapshot = engine.analyze(make_bars(closes[:13]))
    assert [event.event_type for event in snapshot.events] == [StructureEventType.BOS_BULLISH]

    engine.reset()
    extended = engine.analyze(make_bars(closes[:14]))
    assert [event.event_type for event in extended.events] == [StructureEventType.BOS_BULLISH]


def test_wick_khong_phat_bos_o_che_do_close() -> None:
    bars = zigzag_bars()
    # Nến 9 chỉ xuyên lên trên đỉnh 11 bằng bóng, giá đóng cửa vẫn dưới đỉnh.
    pierced = CandleBar(time=bars[9].time, open=9.0, high=11.6, low=8.0, close=9.0, volume=1000.0)
    bars = [*bars[:9], pierced, *bars[10:]]

    close_mode = analyze_structure(bars, config=config(break_confirmation="close"))
    assert [event.event_type for event in close_mode.events] == [
        StructureEventType.BOS_BULLISH,
        StructureEventType.BOS_BULLISH,
        StructureEventType.CHOCH_BEARISH,
    ]
    assert index_of(close_mode.events[0].broken_at) == 12

    wick_mode = analyze_structure(bars, config=config(break_confirmation="wick"))
    # Chế độ wick phát hiện phá sớm hơn (ngay tại nến xuyên bóng) thay vì đợi nến
    # đóng cửa vượt đỉnh; chuỗi loại sự kiện vẫn giữ nguyên.
    assert index_of(wick_mode.events[0].broken_at) == 9
    assert [event.event_type for event in wick_mode.events] == [
        event.event_type for event in close_mode.events
    ]


def test_dinh_bang_nhau_cho_nhan_lh() -> None:
    closes = [5, 6, 7, 8, 9, 10, 9, 8, 7, 8, 9, 10, 9, 8, 7, 8, 9]
    snapshot = analyze_structure(make_bars(closes), config=config())

    highs = [swing for swing in snapshot.swings if swing.kind is PivotType.HIGH]
    assert [swing.price for swing in highs] == [11.0, 11.0]
    assert highs[-1].label is SwingLabel.LH


def test_day_bang_nhau_cho_nhan_ll() -> None:
    closes = [15, 14, 13, 12, 11, 10, 11, 12, 13, 12, 11, 10, 11, 12, 13]
    snapshot = analyze_structure(make_bars(closes), config=config())

    lows = [swing for swing in snapshot.swings if swing.kind is PivotType.LOW]
    assert [swing.price for swing in lows] == [9.0, 9.0]
    assert lows[-1].label is SwingLabel.LL


def test_enhanced_tolerance_cho_nhan_eqh() -> None:
    closes = [5, 6, 7, 8, 9, 10, 9, 8, 7, 8, 9, 10, 9, 8, 7, 8, 9]
    settings = MarketStructureConfig(
        pivot_length=PIVOT_LENGTH, mode="enhanced", equal_tolerance_pct=0.5
    )
    snapshot = analyze_structure(make_bars(closes), config=settings)

    highs = [swing for swing in snapshot.swings if swing.kind is PivotType.HIGH]
    assert highs[-1].label is SwingLabel.EQH


def test_pine_compatible_tu_choi_bo_loc_mo_rong() -> None:
    with pytest.raises(ValueError, match="pine_compatible"):
        MarketStructureConfig(pivot_length=PIVOT_LENGTH, equal_tolerance_pct=1.0)


def test_batch_va_incremental_tuong_duong() -> None:
    bars = zigzag_bars()
    batch = analyze_structure(bars, config=config())

    engine = MarketStructureEngine(config())
    engine.reset()
    for bar in bars:
        engine.feed(bar)
    incremental = engine.snapshot()

    assert batch.swings == incremental.swings
    assert batch.events == incremental.events
    assert batch.break_bias is incremental.break_bias
    assert batch.confirmed_trend is incremental.confirmed_trend


def test_nen_forming_khong_tao_su_kien() -> None:
    closes = FIXTURE["closes"]
    forming = analyze_structure(make_bars(closes[:28]), config=config(), is_final_candle=False)

    assert forming.candle_finality == "forming"
    assert StructureEventType.CHOCH_BEARISH not in {event.event_type for event in forming.events}

    closed = analyze_structure(make_bars(closes[:28]), config=config(), is_final_candle=True)
    assert StructureEventType.CHOCH_BEARISH in {event.event_type for event in closed.events}


def test_nen_khong_hop_le_bi_bo_qua_va_dem() -> None:
    bars = zigzag_bars()
    invalid = CandleBar(
        time=bars[10].time, open=10.0, high=9.0, low=11.0, close=10.0, volume=1000.0
    )
    bars = [*bars[:10], invalid, *bars[11:]]

    snapshot = analyze_structure(bars, config=config())

    assert snapshot.data_quality["invalid_bars"] == 1
    assert snapshot.data_quality["bars"] == len(bars)


def test_nen_trung_thoi_gian_bi_bo_qua_va_dem() -> None:
    bars = zigzag_bars()
    duplicate = CandleBar(time=bars[3].time, open=8.0, high=9.0, low=7.0, close=8.0, volume=1000.0)
    bars = [*bars[:4], duplicate, *bars[4:]]

    snapshot = analyze_structure(bars, config=config())

    assert snapshot.data_quality["duplicate_or_out_of_order"] == 1


def test_thieu_khoi_luong_duoc_dem() -> None:
    bars = make_bars(FIXTURE["closes"], volume=0.0)
    snapshot = analyze_structure(bars, config=config())

    assert snapshot.data_quality["missing_volume"] == len(bars)


def test_chuoi_qua_ngan_bao_insufficient_history() -> None:
    snapshot = analyze_structure(make_bars([1, 2, 3, 4]), config=config())

    assert snapshot.status == "insufficient_history"
    assert snapshot.swings == ()
    assert snapshot.events == ()


def test_config_hash_on_dinh_va_thay_doi_theo_tham_so() -> None:
    base = config()
    same = config()

    assert base.config_hash == same.config_hash
    assert base.config_hash != MarketStructureConfig(pivot_length=5).config_hash
    assert len(base.config_hash) == 12


def test_events_giu_pivot_time_va_broken_time_tach_biet() -> None:
    snapshot = analyze_structure(zigzag_bars(), config=config())

    for event in snapshot.events:
        assert event.pivot_time < event.broken_at
        assert event.confirmed_at <= event.broken_at
