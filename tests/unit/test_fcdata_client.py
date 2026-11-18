"""Test cho FCData client: retry, phân loại lỗi và tham số gửi lên gateway."""

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.exceptions import UpstreamError, UpstreamTimeoutError
from app.fcdata.client import FcDataClient

BASE_URL = "https://fcdata.bookfin.vn"


def make_settings(**overrides) -> Settings:
    """Cấu hình hermetic cho test, không đọc file .env."""
    values = {
        "_env_file": None,
        "fcdata_base_url": BASE_URL,
        "fcdata_max_retries": 1,
        "fcdata_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


@respx.mock
async def test_retry_lai_khi_gap_5xx() -> None:
    route = respx.get(f"{BASE_URL}/health")
    route.side_effect = [
        httpx.Response(503, json={"status": "Error"}),
        httpx.Response(200, json={"ok": True, "service": "bookfin-fcdata"}),
    ]

    async with FcDataClient(make_settings()) as client:
        payload = await client.health()

    assert payload["ok"] is True
    assert route.call_count == 2


@respx.mock
async def test_khong_retry_voi_4xx() -> None:
    route = respx.get(f"{BASE_URL}/health")
    route.mock(return_value=httpx.Response(404, text="Cannot GET /health"))

    async with FcDataClient(make_settings()) as client:
        with pytest.raises(UpstreamError) as excinfo:
            await client.health()

    assert excinfo.value.details["status_code"] == 404
    assert route.call_count == 1


@respx.mock
async def test_timeout_tra_upstream_timeout_error() -> None:
    route = respx.get(f"{BASE_URL}/health")
    route.mock(side_effect=httpx.ReadTimeout("quá hạn"))

    async with FcDataClient(make_settings(fcdata_max_retries=0)) as client:
        with pytest.raises(UpstreamTimeoutError):
            await client.health()

    assert route.call_count == 1


@respx.mock
async def test_status_error_trong_body_van_la_http_200() -> None:
    respx.get(f"{BASE_URL}/api/v1/catalogs/master").mock(
        return_value=httpx.Response(
            200, json={"data": None, "message": "There is no data", "status": "Error"}
        )
    )

    async with FcDataClient(make_settings()) as client:
        with pytest.raises(UpstreamError, match="There is no data"):
            await client.fetch_catalog()


@respx.mock
async def test_json_khong_hop_le_bao_upstream_error() -> None:
    respx.get(f"{BASE_URL}/health").mock(
        return_value=httpx.Response(200, text="<html>khong phai json</html>")
    )

    async with FcDataClient(make_settings(fcdata_max_retries=0)) as client:
        with pytest.raises(UpstreamError, match="JSON"):
            await client.health()


@respx.mock
async def test_batch_stock_prices_gui_du_from_va_to_date() -> None:
    route = respx.get(f"{BASE_URL}/BatchStockPrices").mock(
        return_value=httpx.Response(200, json={"status": 200, "data": [{"Symbol": "FPT"}]})
    )

    async with FcDataClient(make_settings()) as client:
        rows = await client.fetch_batch_stock_prices(["fpt"], start="10/11/2026", end="17/11/2026")

    params = route.calls[0].request.url.params
    assert params["symbols"] == "FPT"
    assert params["fromDate"] == "10/11/2026"
    assert params["toDate"] == "17/11/2026"
    assert params["market"] == "HOSE"
    assert rows == [{"Symbol": "FPT"}]


@respx.mock
async def test_batch_stock_prices_rong_thi_khong_goi_mang() -> None:
    route = respx.get(f"{BASE_URL}/BatchStockPrices")

    async with FcDataClient(make_settings()) as client:
        rows = await client.fetch_batch_stock_prices([], start="10/11/2026", end="17/11/2026")

    assert rows == []
    assert route.call_count == 0


@respx.mock
async def test_daily_ohlc_gui_page_index_va_ascending() -> None:
    route = respx.get(f"{BASE_URL}/DailyOhlc").mock(
        return_value=httpx.Response(200, json={"status": "Success", "data": []})
    )

    async with FcDataClient(make_settings()) as client:
        await client.fetch_daily_ohlc(
            "ssi", start="01/11/2026", end="17/11/2026", page_index=2, page_size=1000
        )

    params = route.calls[0].request.url.params
    assert params["symbol"] == "SSI"
    assert params["pageIndex"] == "2"
    assert params["pageSize"] == "1000"
    assert params["ascending"] == "true"
