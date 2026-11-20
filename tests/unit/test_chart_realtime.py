"""Snapshot fan-out preserves provider OHLCV without tick-volume arithmetic."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.schemas.realtime import ClientMessage
from app.services.chart_realtime_service import ChartRealtimeService
from app.services.realtime_service import MarketChannelAuthorizer, RealtimeService


@pytest.mark.asyncio
async def test_provider_snapshot_is_idempotent_and_symbol_scoped():
    settings = Settings(_env_file=None)
    today = datetime.now(settings.market_tz).strftime("%d/%m/%Y")
    provider = AsyncMock()
    provider.fetch_daily_ohlc.return_value = {
        "data": [
            {
                "TradingDate": today,
                "Open": 20,
                "High": 22,
                "Low": 19,
                "Close": 21,
                "Volume": 1000,
            }
        ]
    }
    hub = RealtimeService(MarketChannelAuthorizer())
    client = hub.connect("test")
    other = hub.connect("other")
    await hub.handle(client, ClientMessage(type="subscribe", channel="market:HPG:1D"))
    await client.queue.get()
    service = ChartRealtimeService(settings, hub, provider)
    for _ in range(2):
        await service.refresh("HPG", {"market:HPG:1D", "market:HPG:1W"})
        event = await client.queue.get()
        assert event.data["volume"] == 1000
        assert event.data["open"] == 20
        assert event.data["state"] == "PROVISIONAL"
        stamp = datetime.fromtimestamp(event.data["timestamp"] / 1000, settings.market_tz)
        assert stamp.hour == 0
    assert other.queue.empty()


@pytest.mark.asyncio
async def test_invalid_and_future_candles_are_not_published():
    settings = Settings(_env_file=None)
    provider = AsyncMock()
    provider.fetch_daily_ohlc.return_value = {
        "data": [
            {
                "TradingDate": "01/01/2099",
                "Open": 20,
                "High": 22,
                "Low": 19,
                "Close": 21,
                "Volume": 1000,
            }
        ]
    }
    hub = RealtimeService(MarketChannelAuthorizer())
    hub.publish = AsyncMock()
    await ChartRealtimeService(settings, hub, provider).refresh("HPG", {"market:HPG:1D"})
    hub.publish.assert_not_called()
