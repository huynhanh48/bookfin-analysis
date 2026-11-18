"""Test cho WebSocket stream: định dạng message, parse tick và gom nến."""

import asyncio
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.fcdata.stream import (
    FcDataStream,
    QuoteTick,
    build_ping_message,
    build_subscribe_message,
    parse_quote_tick,
)
from app.workers.realtime import FormingCandleAggregator

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def make_settings(**overrides) -> Settings:
    """Cấu hình hermetic cho test."""
    values = {
        "_env_file": None,
        "fcdata_ws_url": "wss://example.test/ws",
        "fcdata_ws_ping_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)


class FakeConnection:
    """WebSocket giả: ghi lại message gửi lên và trả lần lượt các frame có sẵn."""

    def __init__(self, frames: list[str]) -> None:
        self.sent: list[dict] = []
        self._frames = list(frames)
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self._frames:
            raise EOFError("hết frame")
        return self._frames.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_subscribe_message_dung_dinh_dang_gateway() -> None:
    payload = json.loads(build_subscribe_message("symbol", [" fpt ", "hpg"]))

    assert payload == {"action": "subscribe", "target": "symbol", "codes": ["FPT", "HPG"]}


def test_subscribe_all_khong_gui_codes() -> None:
    payload = json.loads(build_subscribe_message("all"))

    assert payload == {"action": "subscribe", "target": "all"}


def test_ping_message_dung_dinh_dang_gateway() -> None:
    assert json.loads(build_ping_message()) == {"action": "ping"}


def test_parse_tick_ssi_tho_voi_content_la_chuoi_json() -> None:
    raw = {
        "DataType": "X-TRADE",
        "Content": json.dumps(
            {"Symbol": "fpt", "LastPrice": 123.5, "LastVol": 100, "Time": "09:15:00"}
        ),
    }
    tick = parse_quote_tick(raw)

    assert tick == QuoteTick(
        symbol="FPT", data_type="X-TRADE", price=123.5, volume=100, raw_time="09:15:00"
    )


def test_parse_tick_voi_content_la_object() -> None:
    tick = parse_quote_tick(
        {"DataType": "MI", "Content": {"IndexId": "vnindex", "IndexValue": 1280.5}}
    )

    assert tick is not None
    assert tick.symbol == "VNINDEX"
    assert tick.price == 1280.5


def test_parse_tick_theo_envelope_tai_lieu() -> None:
    tick = parse_quote_tick({"type": "quote", "data": {"Symbol": "SSI", "Close": 19850}})

    assert tick is not None
    assert tick.symbol == "SSI"
    assert tick.price == 19850.0


def test_parse_tick_thieu_ma_tra_none() -> None:
    assert parse_quote_tick({"DataType": "X-TRADE", "Content": {"LastPrice": 1}}) is None


async def test_session_gui_subscribe_va_nhan_tick() -> None:
    ticks: list[QuoteTick] = []

    async def on_tick(tick: QuoteTick) -> None:
        ticks.append(tick)

    frames = [
        json.dumps({"type": "welcome", "protocol": "subscribe_v1", "ssiReady": True}),
        json.dumps({"DataType": "X-TRADE", "Content": {"Symbol": "FPT", "LastPrice": 10}}),
    ]
    connection = FakeConnection(frames)
    stream = FcDataStream(make_settings(), on_tick=on_tick, symbols=["FPT"], indices=["VNINDEX"])
    stop = asyncio.Event()

    with pytest.raises(EOFError):
        await stream._session(connection, stop)

    assert connection.sent[0] == {"action": "subscribe", "target": "symbol", "codes": ["FPT"]}
    assert connection.sent[1] == {"action": "subscribe", "target": "index", "codes": ["VNINDEX"]}
    assert [tick.symbol for tick in ticks] == ["FPT"]


async def test_session_bo_qua_frame_khong_phai_json() -> None:
    ticks: list[QuoteTick] = []

    async def on_tick(tick: QuoteTick) -> None:
        ticks.append(tick)

    frames = [
        json.dumps({"type": "welcome"}),
        "khong-phai-json",
        json.dumps({"type": "pong"}),
    ]
    stream = FcDataStream(make_settings(), on_tick=on_tick)

    with pytest.raises(EOFError):
        await stream._session(FakeConnection(frames), asyncio.Event())

    assert ticks == []


def test_aggregator_tra_nen_khi_sang_khung_moi() -> None:
    aggregator = FormingCandleAggregator(timeframe_minutes=1, market_tz=TZ)
    first = datetime(2026, 11, 17, 9, 15, 10, tzinfo=TZ)
    second = datetime(2026, 11, 17, 9, 16, 5, tzinfo=TZ)

    assert aggregator.on_tick(QuoteTick("FPT", "X-TRADE", price=10.5, volume=100), now=first) == []
    assert len(aggregator.forming_snapshot()) == 1

    closed = aggregator.on_tick(QuoteTick("FPT", "X-TRADE", price=11.0, volume=50), now=second)

    assert len(closed) == 1
    assert closed[0].open == 10.5
    assert closed[0].high == 10.5
    assert closed[0].close == 10.5
    assert closed[0].volume == 100


def test_aggregator_cap_nhat_high_low_volume() -> None:
    aggregator = FormingCandleAggregator(timeframe_minutes=1, market_tz=TZ)
    moment = datetime(2026, 11, 17, 9, 15, 10, tzinfo=TZ)

    aggregator.on_tick(QuoteTick("FPT", "X-TRADE", price=10.0, volume=10), now=moment)
    aggregator.on_tick(QuoteTick("FPT", "X-TRADE", price=12.0, volume=20), now=moment)
    aggregator.on_tick(QuoteTick("FPT", "X-TRADE", price=9.0, volume=5), now=moment)

    candle = aggregator.forming_snapshot()[0]
    assert candle.open == 10.0
    assert candle.high == 12.0
    assert candle.low == 9.0
    assert candle.close == 9.0
    assert candle.volume == 35.0


def test_aggregator_bo_qua_tick_khong_co_gia() -> None:
    aggregator = FormingCandleAggregator(timeframe_minutes=1, market_tz=TZ)

    result = aggregator.on_tick(QuoteTick("FPT", "MI", price=None))

    assert result == []
    assert aggregator.forming_snapshot() == []


def test_bucket_start_cat_theo_phut() -> None:
    aggregator = FormingCandleAggregator(timeframe_minutes=5, market_tz=TZ)
    moment = datetime(2026, 11, 17, 9, 17, 30, tzinfo=TZ)

    assert aggregator.bucket_start(moment) == datetime(2026, 11, 17, 9, 15, tzinfo=TZ)
    assert aggregator.bucket_start(moment).tzinfo == TZ
    assert datetime(2026, 11, 17, 9, 15, tzinfo=TZ).astimezone(UTC).hour == 2
