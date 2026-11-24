"""Realtime screener producer: entered/exited, stale và không phát trùng."""

import json
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.indicators.market_structure import CandleBar
from app.schemas.realtime import ClientMessage
from app.schemas.screener import ScreenerRealtimeEvent, ScreenerRow, ScreenerScanRequest
from app.services.realtime_service import MarketChannelAuthorizer, RealtimeService
from app.services.screener_realtime_service import (
    ScreenerRealtimeBridge,
    ScreenerRealtimeProducer,
)
from app.services.screener_service import fetch_batch_quotes

BASE = datetime(2026, 1, 2, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None):
        del ex
        self.values[key] = value
        return True

    async def publish(self, channel: str, value: str):
        self.published.append((channel, value))
        return 1


class FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def fetch_batch_stock_prices(self, symbols, *, start, end):
        del symbols, start, end
        return self.rows


def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        screener_event_cooldown_seconds=0,
        screener_eval_interval_seconds=1,
        screener_publish_interval_seconds=0.2,
    )


def history() -> dict[str, list[CandleBar]]:
    return {
        "FPT": [
            CandleBar(
                time=BASE + timedelta(days=index),
                open=20_000,
                high=20_000,
                low=20_000,
                close=20_000,
                volume=100_000,
            )
            for index in range(10)
        ]
    }


def quote_row(price: float, volume: int = 200_000) -> dict:
    return {
        "Symbol": "FPT",
        "ClosePrice": price,
        "TotalMatchVol": volume,
        "TradingDate": datetime.now(settings().market_tz).strftime("%d/%m/%Y"),
    }


async def test_previous_session_quote_cannot_be_treated_as_live():
    config = settings()
    today = datetime.now(config.market_tz).date()
    stale = {**quote_row(21_000), "TradingDate": (today - timedelta(days=1)).strftime("%d/%m/%Y")}
    assert await fetch_batch_quotes(FakeClient([stale]), config, ["FPT"], today) == {}


async def loader(symbols, limit, end):
    del symbols, limit, end
    return history()


def producer(client: FakeClient, redis: FakeRedis) -> ScreenerRealtimeProducer:
    return ScreenerRealtimeProducer(
        settings(),
        symbols=["FPT"],
        redis=redis,
        fcdata=client,
        history_loader=loader,
    )


async def test_entered_then_exited_and_no_duplicate_events():
    redis = FakeRedis()
    client = FakeClient([quote_row(20_600)])
    instance = producer(client, redis)
    await instance._refresh_history(force=True)

    await instance._evaluate(["FPT"])
    instance._last_publish = 0
    await instance._publish_if_needed()
    assert len(redis.published) == 1
    event = ScreenerRealtimeEvent.model_validate_json(redis.published[0][1])
    assert [row.symbol for row in event.entered] == ["FPT"]
    assert event.exited == []

    # Cùng điều kiện, không được phát entered lần nữa.
    await instance._evaluate(["FPT"])
    instance._last_publish = 0
    await instance._publish_if_needed()
    event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
    assert event.entered == []

    client.rows = [quote_row(20_600, 50_000)]
    await instance._evaluate(["FPT"])
    assert "FPT" in instance._rows

    client.rows = [quote_row(20_000, 200_000)]
    await instance._evaluate(["FPT"])
    instance._last_publish = 0
    await instance._publish_if_needed()
    event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
    assert event.exited == ["FPT"]


async def test_new_profile_preserves_http_match_when_quote_is_missing():
    redis = FakeRedis()
    instance = producer(FakeClient([quote_row(20_600)]), redis)
    await instance._refresh_history(force=True)
    await instance._evaluate(["FPT"])
    row = instance._rows["FPT"]
    now = datetime.now(UTC).isoformat()
    await redis.set(
        "screener:active_profile",
        json.dumps(
            {
                "profile_id": "new-profile",
                "request": ScreenerScanRequest(mode="realtime").model_dump(mode="json"),
                "symbols": ["FPT"],
                "snapshot": {
                    "profile_id": "new-profile",
                    "mode": "realtime",
                    "as_of": now,
                    "generated_at": now,
                    "feed_status": "LIVE",
                    "total_universe": 1,
                    "evaluated": 1,
                    "matched": 1,
                    "skipped": 0,
                    "results": [row.model_dump(mode="json")],
                },
            }
        ),
    )
    await instance._reload_profile()
    await instance._publish_if_needed()
    event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
    assert [row.symbol for row in event.rows] == ["FPT"]
    assert event.scan_seed_at == datetime.fromisoformat(now)


async def test_missing_quote_marks_stale_and_does_not_emit_false_exit():
    redis = FakeRedis()
    client = FakeClient([quote_row(20_600)])
    instance = producer(client, redis)
    await instance._refresh_history(force=True)
    await instance._evaluate(["FPT"])
    instance._last_publish = 0
    await instance._publish_if_needed()

    client.rows = []
    await instance._evaluate(["FPT"])
    instance._last_publish = 0
    await instance._publish_if_needed()
    event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
    assert event.feed_status == "STALE"
    assert event.exited == []
    assert instance._matched == {"FPT"}


async def test_bridge_forwards_entered_exited_and_update():
    hub = RealtimeService(MarketChannelAuthorizer())
    bridge = ScreenerRealtimeBridge(FakeRedis(), hub)  # type: ignore[arg-type]
    connection = hub.connect("development")
    await hub.handle(
        connection,
        ClientMessage(type="subscribe", channel="screener:abc"),
    )
    # Xoá ack.
    await connection.queue.get()

    row = ScreenerRow(
        symbol="FPT",
        price=20_600,
        change_percent=3,
        volume=200_000,
        volume_sma=100_000,
        relative_volume=2,
        ma5=20_120,
        price_vs_ma5_percent=2.4,
        status="LIVE",
        updated_at=datetime.now(tz=UTC),
        matched_conditions=["DK1", "DK2", "DK3", "DK4", "DK6"],
        divergence="Bullish hidden · RSI",
    )
    event = ScreenerRealtimeEvent(
        profile_id="abc",
        rows=[row],
        entered=[row],
        exited=["HPG"],
        feed_status="LIVE",
        generated_at=datetime.now(tz=UTC),
    )
    await bridge._forward(event.model_dump_json())
    types = []
    while not connection.queue.empty():
        message = await connection.queue.get()
        types.append(message.type)
        if message.type == "screener.update":
            assert message.data["rows"][0]["divergence"] == row.divergence
    assert types == ["SCREENER_ENTERED", "SCREENER_EXITED", "screener.update"]
    await hub.close()
