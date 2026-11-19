"""Test tích hợp: cần PostgreSQL đang chạy.

Chạy bằng `pytest -m integration` (đã tách khỏi CI mặc định).
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_ready_voi_database_that(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["checks"]["database"]["ok"] is True


async def test_symbols_doc_tu_postgres(client: AsyncClient) -> None:
    response = await client.get("/api/v1/market/symbols", params={"limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 5
    assert payload["total"] >= 0
    assert len(payload["items"]) <= 5


async def test_ma_khong_ton_tai_tra_404_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/market/candles/KHONGTONTAI")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_candles_va_indicators_cua_fpt(client: AsyncClient) -> None:
    candles = await client.get("/api/v1/market/candles/FPT", params={"limit": 3})
    if candles.status_code == 404:
        pytest.skip("Database chưa có dữ liệu FPT; cần chạy worker backfill trước.")

    assert candles.status_code == 200
    payload = candles.json()
    assert payload["count"] == 3
    assert payload["candles"][0]["open"] > 0

    structure = await client.get("/api/v1/indicators/FPT/structure")
    assert structure.status_code == 200
    body = structure.json()
    assert body["symbol"] == "FPT"
    assert body["break_bias"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert body["confirmed_trend"] in {"UPTREND", "DOWNTREND", "SIDEWAYS", "TRANSITION"}


async def test_series_chi_bao_can_cung_truc_thoi_gian(client: AsyncClient) -> None:
    response = await client.get("/api/v1/indicators/FPT/series", params={"limit": 60})
    if response.status_code == 404:
        pytest.skip("Database chưa có dữ liệu FPT; cần chạy worker backfill trước.")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 60
    assert len(payload["candles"]) == 60

    sma = next(series for series in payload["series"] if series["key"] == "sma(period=20)")
    assert sma["points"][-1]["time"] == payload["candles"][-1]["time"]
    assert sma["points"][-1]["values"]["sma"] is not None


async def test_series_chi_bao_tra_400_khi_sai_ten(client: AsyncClient) -> None:
    response = await client.get("/api/v1/indicators/FPT/series", params={"indicator": "foo:1"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
