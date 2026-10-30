"""Cook-along scaling and per-ingredient overrides — ADR 0022.

The two rules most easily implemented backwards, pinned here:

  * ``servings_made`` is ABSOLUTE (§1) — 2 servings of a recipe yielding 4
    deducts HALF, not double. The legacy ``servings`` field stays a batch
    multiplier so the MCP agent tool keeps working.
  * an override's quantity is the FINAL amount (§2) — scaling is NOT applied
    on top of it.
"""

from __future__ import annotations

from decimal import Decimal

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _setup(client: httpx.Client, h: dict, *, stock: float = 100.0) -> tuple[str, str, str]:
    """A 4-serving recipe using 8 g of flour total, with `stock` g on hand."""
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = client.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc, "quantity": stock, "unit": "g"},
        headers=h,
    )
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Flatbread",
            "servings": 4,
            "instructions": "1. Mix.\n2. Bake.",
            "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}],
        },
        headers=h,
    ).json()
    return loc, item["id"], recipe["id"]


def _on_hand(client: httpx.Client, h: dict, loc: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]
    row = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(row["quantity"])) if row else Decimal("0")


def test_servings_made_is_absolute_not_a_multiplier(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)

    # 2 servings of a 4-serving recipe = half of 8 g = 4 g.
    resp = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings_made": 2, "location_id": loc}, headers=h)
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("96")  # 100 - 4


def test_legacy_servings_stays_a_batch_multiplier(client: httpx.Client) -> None:
    """The MCP agent tool passes `servings`; its meaning must not have shifted."""
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)

    # 2 batches of the whole 8 g recipe = 16 g.
    resp = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 2, "location_id": loc}, headers=h)
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("84")  # 100 - 16


def test_servings_made_wins_when_both_are_sent(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    resp = client.post(
        f"/api/recipes/{recipe_id}/cook", json={"servings": 9, "servings_made": 2, "location_id": loc}, headers=h
    )
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("96")  # scaled, not 9 batches


def test_fractional_servings_scale(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    # 1.5 / 4 * 8 g = 3 g.
    resp = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings_made": 1.5, "location_id": loc}, headers=h)
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("97")


def test_override_quantity_is_the_final_amount_not_rescaled(client: httpx.Client) -> None:
    """The rule that's easy to get backwards: scale 0.5 PLUS an override of 20 g
    must deduct 20 g — not 10 g. The user typed 20 while looking at the already
    scaled figure."""
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    ing_id = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["ingredients"][0]["ingredient_id"]

    resp = client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={
            "servings_made": 2,  # scale 0.5
            "location_id": loc,
            "ingredient_overrides": [{"ingredient_id": ing_id, "quantity": 20, "unit": "g"}],
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("80")  # 100 - 20, NOT 100 - 10


def test_omitting_an_ingredient_deducts_nothing(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id, recipe_id = _setup(client, h)
    ing_id = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["ingredients"][0]["ingredient_id"]

    resp = client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={"location_id": loc, "ingredient_overrides": [{"ingredient_id": ing_id, "omit": True}]},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert _on_hand(client, h, loc, item_id) == Decimal("100")  # untouched


def test_omitting_an_out_of_stock_ingredient_does_not_block_the_cook(client: httpx.Client) -> None:
    """Omission must skip the shortfall check too — that's the whole point of
    'I'm leaving it out'."""
    h = _admin_headers(client)
    loc, _item_id, recipe_id = _setup(client, h, stock=0)
    ing_id = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["ingredients"][0]["ingredient_id"]

    blocked = client.post(f"/api/recipes/{recipe_id}/cook", json={"location_id": loc}, headers=h)
    assert blocked.status_code == 400  # short, as expected

    ok = client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={"location_id": loc, "ingredient_overrides": [{"ingredient_id": ing_id, "omit": True}]},
        headers=h,
    )
    assert ok.status_code == 200, ok.text


def test_cook_event_records_what_was_actually_made(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, _item_id, recipe_id = _setup(client, h)
    ing_id = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["ingredients"][0]["ingredient_id"]
    client.post(
        f"/api/recipes/{recipe_id}/cook",
        json={
            "servings_made": 2,
            "location_id": loc,
            "ingredient_overrides": [{"ingredient_id": ing_id, "omit": True}],
        },
        headers=h,
    )

    from app.db.session import SessionLocal
    from app.models import RecipeCookEvent

    with SessionLocal() as db:
        event = db.query(RecipeCookEvent).one()
        assert Decimal(str(event.servings_made)) == Decimal("2")
        assert event.ingredient_overrides == [
            {"ingredient_id": ing_id, "quantity": None, "unit": None, "omit": True}
        ]


def test_availability_scales_to_servings_made(client: httpx.Client) -> None:
    """8 g needed as written, only 5 g on hand: short at full size, fine at half."""
    h = _admin_headers(client)
    _loc, _item_id, recipe_id = _setup(client, h, stock=5)

    as_written = client.get(f"/api/recipes/{recipe_id}/availability", headers=h).json()
    assert as_written["ingredients"][0]["status"] == "missing"

    halved = client.get(f"/api/recipes/{recipe_id}/availability?servings_made=2", headers=h).json()
    assert halved["ingredients"][0]["status"] == "have"  # needs 4 g, has 5


def test_scaled_amounts_are_readable_not_28_digit_decimals(client: httpx.Client) -> None:
    """Dividing by the base yield gives Decimal's full expansion (a third of 1
    each = 0.3333...333, 28 digits) — unreadable, and unusable in a number
    input. Display amounts are quantized to 3dp."""
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "P"}, headers=h).json()["id"]
    client.post("/api/items", json={"name": "Bay Leaf"}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={"name": "Stew", "servings": 6, "ingredients": [{"name": "Bay Leaf", "quantity": 1, "unit": "each"}]},
        headers=h,
    ).json()
    assert loc

    rows = client.get(f"/api/recipes/{recipe['id']}/availability?servings_made=2", headers=h).json()
    assert rows["ingredients"][0]["quantity_required"] == "0.333"

    # A clean division stays clean — no stray trailing zeros either.
    whole = client.get(f"/api/recipes/{recipe['id']}/availability?servings_made=3", headers=h).json()
    assert whole["ingredients"][0]["quantity_required"] == "0.5"

    exact = client.get(f"/api/recipes/{recipe['id']}/availability?servings_made=6", headers=h).json()
    assert exact["ingredients"][0]["quantity_required"] == "1"


def test_output_yield_scales_with_servings_made(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, _item_id, recipe_id = _setup(client, h)
    out = client.post("/api/items", json={"name": "Flatbread Loaf"}, headers=h).json()
    client.put(
        f"/api/recipes/{recipe_id}",
        json={"name": "Flatbread", "servings": 4, "output_item_id": out["id"], "output_quantity": 4, "output_unit": "each"},
        headers=h,
    )

    resp = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings_made": 2, "location_id": loc}, headers=h)
    assert resp.status_code == 200, resp.text
    assert Decimal(str(resp.json()["produced_quantity"])) == Decimal("2")  # half of 4
