"""Integration: screener historical scan trên dữ liệu PostgreSQL thật."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_default_filters_match_afl_strategy(client: AsyncClient):
    response = await client.get("/api/v1/screener/filters/default")
    assert response.status_code == 200
    payload = response.json()
    assert payload["min_volume"] == {"enabled": True, "value": 100_000}
    assert payload["min_price"] == {"enabled": True, "value": 10_000.0}
    assert payload["relative_volume"] == {
        "enabled": True,
        "value": 0.8,
        "lookback": 10,
    }
    assert payload["price_change"] == {"enabled": True, "percent": 2.0}
    assert payload["price_vs_ma5"] == {"enabled": True, "percent": 2.0, "period": 5}
    assert payload["divergence"] == {
        "enabled": False,
        "direction": "bullish",
        "kind": "regular",
        "window": "latest",
        "days": 1,
        "basis": "pivot",
        "date_from": None,
        "date_to": None,
    }


async def test_historical_scan_symbols_and_all_toggles(client: AsyncClient):
    response = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "historical",
            "universe": "symbols",
            "symbols": ["FPT", "HPG", "SSI"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "historical"
    assert payload["total_universe"] == 3
    assert payload["evaluated"] + payload["skipped"] == 3
    assert payload["matched"] == len(payload["results"])
    for row in payload["results"]:
        assert row["status"] == "CONFIRMED"
        assert row["symbol"] in {"FPT", "HPG", "SSI"}
        assert row["price"] > 0
        assert row["matched_conditions"] == ["DK1", "DK2", "DK3", "DK4", "DK6"]


async def test_scan_can_disable_all_conditions(client: AsyncClient):
    response = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "historical",
            "universe": "symbols",
            "symbols": ["FPT"],
            "conditions": {
                "min_volume": {"enabled": False, "value": 100_000},
                "min_price": {"enabled": False, "value": 10_000},
                "relative_volume": {"enabled": False, "value": 0.8, "lookback": 10},
                "price_change": {"enabled": False, "percent": 2},
                "price_vs_ma5": {"enabled": False, "percent": 2, "period": 5},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["matched"] == 0


async def test_realtime_scan_returns_status_and_caches_results(client: AsyncClient):
    response = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "realtime",
            "universe": "symbols",
            "symbols": ["FPT"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "realtime"
    assert payload["feed_status"] in {"LIVE", "STALE"}
    assert payload["profile_id"]

    cached = await client.get(
        "/api/v1/screener/results", params={"profile_id": payload["profile_id"]}
    )
    assert cached.status_code == 200
    assert cached.json()["profile_id"] == payload["profile_id"]


async def test_divergence_filter_can_be_enabled(client: AsyncClient):
    response = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "historical",
            "universe": "symbols",
            "symbols": ["FPT", "HPG"],
            "conditions": {
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "days": 10,
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evaluated"] + payload["skipped"] == 2
    for row in payload["results"]:
        assert "DIVERGENCE" in row["matched_conditions"]
        assert row["divergence"]


async def test_divergence_latest_and_custom_range(client: AsyncClient):
    today_payload = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "historical",
            "universe": "symbols",
            "symbols": ["FPT"],
            "conditions": {
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "window": "latest",
                }
            },
        },
    )
    assert today_payload.status_code == 200, today_payload.text

    range_payload = await client.post(
        "/api/v1/screener/scan",
        json={
            "mode": "historical",
            "universe": "symbols",
            "symbols": ["FPT", "HPG"],
            "conditions": {
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "window": "range",
                    "date_from": "2026-11-01",
                    "date_to": "2026-11-20",
                }
            },
        },
    )
    assert range_payload.status_code == 200, range_payload.text


async def test_divergence_pivot_date_waits_for_confirmation(client: AsyncClient):
    """Tín hiệu chỉ xuất hiện sau ngày xác nhận, không dùng dữ liệu tương lai."""

    def payload(symbol: str, as_of: str, basis: str, pivot_date: str) -> dict:
        return {
            "mode": "historical",
            "universe": "symbols",
            "symbols": [symbol],
            "as_of": f"{as_of}T16:00:00+07:00",
            "conditions": {
                "min_volume": {"enabled": False, "value": 100_000},
                "min_price": {"enabled": False, "value": 10_000},
                "relative_volume": {"enabled": False, "value": 0.8, "lookback": 10},
                "price_change": {"enabled": False, "percent": 2},
                "price_vs_ma5": {"enabled": False, "percent": 2, "period": 5},
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "window": "range",
                    "basis": basis,
                    "date_from": pivot_date,
                    "date_to": pivot_date,
                },
            },
        }

    cases = (
        ("VHM", "2026-11-09", "2026-11-19"),
        ("SSI", "2026-11-02", "2026-11-12"),
    )
    for symbol, pivot_date, confirmed_date in cases:
        before = await client.post(
            "/api/v1/screener/scan",
            json=payload(symbol, pivot_date, "pivot", pivot_date),
        )
        assert before.status_code == 200, before.text
        assert before.json()["matched"] == 0, before.text

        after = await client.post(
            "/api/v1/screener/scan",
            json=payload(symbol, confirmed_date, "pivot", pivot_date),
        )
        assert after.status_code == 200, after.text
        body = after.json()
        assert body["matched"] == 1, body
        assert body["results"][0]["symbol"] == symbol

        confirmed = await client.post(
            "/api/v1/screener/scan",
            json=payload(symbol, confirmed_date, "confirmed", confirmed_date),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["matched"] == 1, confirmed.text
