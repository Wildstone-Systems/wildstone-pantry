"""Recipe suggestions carry the counts behind their score (#30).

The meal plan's recipe picker shows "1/2 on hand"; the score alone can't say
how many ingredients that is. Optional lines stay out of both counts.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _measured(client: httpx.Client, name: str) -> str:
    resp = client.post(
        "/api/items",
        json={"name": name, "stock_mode": "measured", "size_quantity": 1, "size_unit": "kg", "density_g_per_ml": 0.6},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_suggestion_counts_match_the_score(client: httpx.Client) -> None:
    flour, sugar, salt = _measured(client, "Flour"), _measured(client, "Sugar"), _measured(client, "Salt")
    loc = client.post("/api/locations", json={"name": "Pantry"}).json()["id"]
    added = client.post(
        "/api/inventory/add", json={"item_id": flour, "location_id": loc, "quantity": 500, "unit": "g"}
    )
    assert added.status_code in (200, 201), added.text

    recipe = client.post("/api/recipes", json={"name": "Cake"}).json()["id"]
    put = client.put(
        f"/api/recipes/{recipe}/ingredients",
        json=[
            {"item_id": flour, "quantity_required": 1, "unit": "cup"},
            {"item_id": sugar, "quantity_required": 1, "unit": "cup"},
            {"item_id": salt, "quantity_required": 1, "unit": "tsp", "optional": True},
        ],
    )
    assert put.status_code in (200, 201), put.text

    rows = client.get("/api/recipes/suggestions").json()["suggestions"]
    cake = next(r for r in rows if r["recipe_id"] == recipe)
    assert (cake["have_count"], cake["needed_count"]) == (1, 2)
    assert cake["available_score"] == pytest.approx(0.5)
    assert cake["missing_items"] == ["Sugar"]
