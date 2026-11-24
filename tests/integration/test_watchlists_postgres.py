"""Integration: watchlist mặc định, CRUD và bulk add trên PostgreSQL."""

from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_default_watchlists_are_seeded_and_protected(client: AsyncClient):
    response = await client.get("/api/v1/watchlists")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 10
    defaults = [item for item in payload["items"] if item["is_default"]]
    assert len(defaults) >= 10
    # Default list được phép rename; chỉ cần vẫn đủ 10 và đánh dấu is_default.
    assert all(item["name"] for item in defaults)

    # Gọi lại không được nhân đôi default list.
    again = await client.get("/api/v1/watchlists")
    assert again.json()["total"] == payload["total"]

    delete_default = await client.delete(f"/api/v1/watchlists/{defaults[0]['id']}")
    assert delete_default.status_code == 400


async def test_watchlist_crud_bulk_add_and_clear(client: AsyncClient):
    name = f"Test {uuid4().hex[:8]}"
    created = await client.post("/api/v1/watchlists", json={"name": name})
    assert created.status_code == 201
    watchlist = created.json()
    watchlist_id = watchlist["id"]

    renamed = await client.patch(
        f"/api/v1/watchlists/{watchlist_id}", json={"name": f"{name} renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == f"{name} renamed"

    added = await client.post(
        f"/api/v1/watchlists/{watchlist_id}/items/bulk",
        json={"symbols": ["FPT", "HPG", "FPT"]},
    )
    assert added.status_code == 200
    body = added.json()
    assert body["added"] == 2
    assert body["skipped"] == 0
    assert body["symbols"] == ["FPT", "HPG"]

    duplicate = await client.post(
        f"/api/v1/watchlists/{watchlist_id}/items/bulk",
        json={"symbols": ["FPT"]},
    )
    assert duplicate.json()["added"] == 0
    assert duplicate.json()["skipped"] == 1

    unknown = await client.post(
        f"/api/v1/watchlists/{watchlist_id}/items/bulk",
        json={"symbols": ["KHONGCOMA"]},
    )
    assert unknown.status_code == 400

    items = await client.get(f"/api/v1/watchlists/{watchlist_id}/items")
    assert items.status_code == 200
    assert [item["symbol"] for item in items.json()["items"]] == ["FPT", "HPG"]

    removed = await client.delete(f"/api/v1/watchlists/{watchlist_id}/items/FPT")
    assert removed.status_code == 204
    after_remove = await client.get(f"/api/v1/watchlists/{watchlist_id}/items")
    assert [item["symbol"] for item in after_remove.json()["items"]] == ["HPG"]

    cleared = await client.delete(f"/api/v1/watchlists/{watchlist_id}/items")
    assert cleared.status_code == 204
    after_clear = await client.get(f"/api/v1/watchlists/{watchlist_id}/items")
    assert after_clear.json()["count"] == 0

    deleted = await client.delete(f"/api/v1/watchlists/{watchlist_id}")
    assert deleted.status_code == 204


async def test_duplicate_watchlist_name_is_rejected(client: AsyncClient):
    name = f"Dup {uuid4().hex[:8]}"
    first = await client.post("/api/v1/watchlists", json={"name": name})
    assert first.status_code == 201
    second = await client.post("/api/v1/watchlists", json={"name": name})
    assert second.status_code == 409
    await client.delete(f"/api/v1/watchlists/{first.json()['id']}")
