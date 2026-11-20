"""Realtime isolation, slow-consumer handling and wire authentication regressions."""

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.adapters.websocket_adapter import serve_websocket
from app.core.config import Settings
from app.schemas.realtime import ClientMessage, RealtimeEvent
from app.services.realtime_service import MarketChannelAuthorizer, RealtimeService


async def test_channel_isolation_unsubscribe_and_private_denial():
    hub = RealtimeService(MarketChannelAuthorizer())
    a, b = hub.connect("admin"), hub.connect("admin")
    await hub.handle(a, ClientMessage(type="subscribe", channel="market:SSI:1D"))
    assert (await a.queue.get()).type == "ack"
    await hub.handle(b, ClientMessage(type="subscribe", channel="chat:private"))
    assert (await b.queue.get()).data["code"] == "channel_forbidden"
    assert await hub.publish(RealtimeEvent(type="candle.updated", channel="market:SSI:1D")) == 1
    assert b.queue.empty()
    await a.queue.get()
    await hub.handle(a, ClientMessage(type="unsubscribe", channel="market:SSI:1D"))
    await a.queue.get()
    assert await hub.publish(RealtimeEvent(type="candle.updated", channel="market:SSI:1D")) == 0
    await hub.close()
    assert not hub.connections and a.stopped.is_set()


async def test_slow_client_does_not_block_healthy_client():
    hub = RealtimeService(MarketChannelAuthorizer(), queue_size=1)
    a, b = hub.connect("admin"), hub.connect("admin")
    a.channels.add("market:SSI:quote")
    b.channels.add("market:SSI:quote")
    hub.send(a, RealtimeEvent(type="occupied"))
    assert await hub.publish(RealtimeEvent(type="quote", channel="market:SSI:quote")) == 1
    assert a.close_code == 1013 and a.id not in hub.connections
    assert (await b.queue.get()).type == "quote"


def transport_app():
    app = FastAPI()
    hub = RealtimeService(MarketChannelAuthorizer())
    settings = Settings(_env_file=None, app_env="production", admin_api_key="test-secret")

    @app.websocket("/ws")
    async def endpoint(socket: WebSocket):
        await serve_websocket(socket, hub, settings)

    return app, hub


def test_browser_auth_ping_validation_and_cleanup():
    app, hub = transport_app()
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws", headers={"origin": "http://localhost:5173"}) as ws,
    ):
        ws.send_json({"type": "authenticate", "token": "test-secret"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "ping", "request_id": "one"})
        assert ws.receive_json()["request_id"] == "one"
        ws.send_text("not json")
        assert ws.receive_json()["data"]["code"] == "invalid_message"
        ws.send_json({"type": "subscribe", "channel": "market:SSI:1D"})
        assert ws.receive_json()["type"] == "ack"
    assert not hub.connections


def test_bad_token_and_origin_are_rejected():
    app, hub = transport_app()
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", headers={"origin": "https://untrusted.example"}),
        ):
            pass
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "authenticate", "token": "wrong"})
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_json()
            assert error.value.code == 1008
    assert not hub.connections
