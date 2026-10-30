"""Ad hoc cooking — ADR 0024.

The rules most easily lost, pinned here:

  * a draft is invisible everywhere finished recipes appear (§1) — the list,
    suggestions — and visible only via the ad hoc endpoints;
  * repeated ingredient uses MERGE into one line, steps append as sequential
    numbered lines (§2) — the canonical cook-along shape;
  * Finish deducts to zero and reports shortfalls instead of refusing (§3) —
    the ledger doesn't get to veto a cook that already happened;
  * discard keeps the deductions but deletes the draft and logs no cook
    event (§4).
"""

from __future__ import annotations

from decimal import Decimal

import httpx


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _setup_stock(client: httpx.Client, h: dict, *, grams: float = 100.0) -> tuple[str, str]:
    """A location holding `grams` g of flour. Returns (location_id, item_id)."""
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = client.post(
        "/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h
    ).json()
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc, "quantity": grams, "unit": "g"},
        headers=h,
    )
    return loc, item["id"]


def _on_hand(client: httpx.Client, h: dict, loc: str, item_id: str) -> Decimal:
    rows = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]
    row = next((r for r in rows if r["item_id"] == item_id), None)
    return Decimal(str(row["quantity"])) if row else Decimal("0")


def _start(client: httpx.Client, h: dict, **payload) -> dict:
    resp = client.post("/api/recipes/ad-hoc", json=payload, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _append(client: httpx.Client, h: dict, recipe_id: str, **entry) -> httpx.Response:
    return client.post(f"/api/recipes/{recipe_id}/ad-hoc/entries", json=entry, headers=h)


# ---------------------------------------------------------------- visibility


def test_draft_is_invisible_to_recipe_list_and_suggestions(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h, name="Tuesday improv")
    recipe_id = state["recipe"]["id"]
    assert state["recipe"]["is_draft"] is True

    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 8, "unit": "g"})

    listed = {r["id"] for r in client.get("/api/recipes", headers=h).json()}
    assert recipe_id not in listed, "a half-captured draft leaked into the recipe list"

    suggested = {s["recipe_id"] for s in client.get("/api/recipes/suggestions", headers=h).json()["suggestions"]}
    assert recipe_id not in suggested, "a draft leaked into recipe suggestions"

    drafts = {r["id"] for r in client.get("/api/recipes/ad-hoc", headers=h).json()}
    assert recipe_id in drafts

    # Direct by-id access deliberately works — the capture UI is built from it.
    assert client.get(f"/api/recipes/{recipe_id}", headers=h).status_code == 200


def test_abandoning_a_draft_costs_nothing(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id = _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 8, "unit": "g"})

    assert client.delete(f"/api/recipes/{recipe_id}", headers=h).status_code in (200, 204)
    assert _on_hand(client, h, loc, item_id) == Decimal("100"), "abandon must not deduct"
    assert client.get("/api/recipes/ad-hoc", headers=h).json() == []


# ------------------------------------------------------------------- capture


def test_repeat_ingredient_uses_merge_and_steps_number_sequentially(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]

    # One action carrying both halves: ingredient + step (ADR 0024 §2).
    resp = _append(
        client, h, recipe_id,
        ingredient={"name": "Flour", "quantity": 100, "unit": "g"},
        step_text="Whisk the flour into the stock.",
    )
    assert resp.status_code == 200, resp.text
    _append(client, h, recipe_id, step_text="Simmer 10 minutes.")
    resp = _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 50, "unit": "g"})
    assert resp.status_code == 200, resp.text

    state = resp.json()
    lines = state["ingredients"]
    assert len(lines) == 1, "the same item twice must merge into one line"
    assert Decimal(lines[0]["quantity_required"]) == Decimal("150")
    assert state["recipe"]["instructions"] == "1. Whisk the flour into the stock.\n2. Simmer 10 minutes."


def test_one_action_can_use_several_ingredients(client: httpx.Client) -> None:
    """A dressing is ONE action using three ingredients — one entry, one step
    (ADR 0024 §2), not three round-trips with the step glued to one of them."""
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]

    resp = _append(
        client, h, recipe_id,
        ingredients=[
            {"name": "Olive oil", "quantity": 60, "unit": "ml"},
            {"name": "Red wine vinegar", "quantity": 20, "unit": "ml"},
            {"name": "Dijon mustard", "quantity": 1, "unit": "tsp"},
        ],
        step_text="Whisk into a dressing.",
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert {l["item_name"] for l in state["ingredients"]} == {
        "Olive oil", "Red wine vinegar", "Dijon mustard",
    }
    # One action → ONE step, not one per ingredient.
    assert state["recipe"]["instructions"] == "1. Whisk into a dressing."


def test_duplicates_within_a_batch_merge_before_touching_the_draft(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]

    resp = _append(
        client, h, recipe_id,
        ingredients=[
            {"name": "Flour", "quantity": 100, "unit": "g"},
            {"name": "Flour", "quantity": 50, "unit": "g"},
        ],
    )
    assert resp.status_code == 200, resp.text
    lines = resp.json()["ingredients"]
    assert len(lines) == 1
    assert Decimal(lines[0]["quantity_required"]) == Decimal("150")


def test_singular_ingredient_field_still_folds_in(client: httpx.Client) -> None:
    """`ingredient` (singular) is convenience sugar for the one-thing case —
    it must land identically to a one-element `ingredients` list."""
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]

    resp = _append(
        client, h, recipe_id,
        ingredient={"name": "Flour", "quantity": 30, "unit": "g"},
        ingredients=[{"name": "Butter", "quantity": 15, "unit": "g"}],
    )
    assert resp.status_code == 200, resp.text
    assert {l["item_name"] for l in resp.json()["ingredients"]} == {"Flour", "Butter"}


def test_entry_needs_ingredient_or_step(client: httpx.Client) -> None:
    h = _admin_headers(client)
    state = _start(client, h)
    assert _append(client, h, state["recipe"]["id"]).status_code == 422


def test_appending_to_a_finished_recipe_is_refused(client: httpx.Client) -> None:
    h = _admin_headers(client)
    recipe = client.post("/api/recipes", json={"name": "Real recipe", "servings": 2}, headers=h).json()
    resp = _append(client, h, recipe["id"], step_text="Sneaky step")
    assert resp.status_code == 400
    assert "draft" in resp.json()["detail"].lower()


def test_seed_entries_load_at_start(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(
        client, h,
        entries=[{"ingredient": {"name": "Flour", "quantity": 20, "unit": "g"}}],
    )
    assert len(state["ingredients"]) == 1
    assert Decimal(state["ingredients"][0]["quantity_required"]) == Decimal("20")


# -------------------------------------------------------------------- finish


def test_finish_save_promotes_deducts_and_logs_the_cook(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id = _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 40, "unit": "g"},
            step_text="Make a roux.")

    resp = client.post(
        f"/api/recipes/{recipe_id}/ad-hoc/finish",
        json={"servings_made": 3, "save": True, "name": "Tuesday roux"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["cook_logged"] is True and body["cook_event_id"]
    assert body["shortfalls"] == [] and body["unconvertible"] == []
    assert _on_hand(client, h, loc, item_id) == Decimal("60")

    # Promoted: named, real yield, in the list, out of the drafts, cookable again.
    recipe = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["recipe"]
    assert recipe["is_draft"] is False
    assert recipe["name"] == "Tuesday roux"
    assert recipe["servings"] == 3
    assert recipe_id in {r["id"] for r in client.get("/api/recipes", headers=h).json()}
    assert client.get("/api/recipes/ad-hoc", headers=h).json() == []


def test_finish_save_requires_a_name(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _setup_stock(client, h)
    state = _start(client, h)  # left on the "Untitled cook" default
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 10, "unit": "g"})

    resp = client.post(f"/api/recipes/{recipe_id}/ad-hoc/finish", json={"save": True}, headers=h)
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_finish_discard_keeps_deductions_but_no_recipe_no_cook_event(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc, item_id = _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 25, "unit": "g"})

    resp = client.post(f"/api/recipes/{recipe_id}/ad-hoc/finish", json={"save": False}, headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is False
    assert body["cook_logged"] is False and body["cook_event_id"] is None
    assert len(body["deducted_transaction_ids"]) == 1

    assert _on_hand(client, h, loc, item_id) == Decimal("75"), "discard must keep the deductions"
    assert client.get(f"/api/recipes/{recipe_id}", headers=h).status_code == 404
    assert client.get("/api/recipes/ad-hoc", headers=h).json() == []


def test_finish_deducts_to_zero_and_reports_shortfall_instead_of_refusing(client: httpx.Client) -> None:
    """ADR 0024 §3 — the sharpest divergence from the recipe cook path. The
    same amounts through POST /recipes/{id}/cook would 400 with 'Insufficient
    inventory'; here the cook already happened, so the ledger empties and the
    gap is surfaced as drift."""
    h = _admin_headers(client)
    loc, item_id = _setup_stock(client, h, grams=5.0)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 8, "unit": "g"})

    resp = client.post(
        f"/api/recipes/{recipe_id}/ad-hoc/finish",
        json={"servings_made": 1, "save": True, "name": "Scraped the barrel"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _on_hand(client, h, loc, item_id) == Decimal("0"), "what WAS there must be spent"
    assert len(body["shortfalls"]) == 1
    short = body["shortfalls"][0]
    assert short["item_name"] == "Flour"
    assert Decimal(str(short["short"])) == Decimal("3")
    assert body["cook_logged"] is True, "a shortfall must not block the cook record"


def test_finish_reports_unconvertible_lines_and_deducts_nothing_for_them(client: httpx.Client) -> None:
    """No unit bridge (cups of an item stocked in grams, no density): deduct
    nothing for that line rather than guess, and say so — ADR 0017 semantics
    carried into the partial plan."""
    h = _admin_headers(client)
    loc, item_id = _setup_stock(client, h)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    _append(client, h, recipe_id, ingredient={"name": "Flour", "quantity": 1, "unit": "cup"})

    resp = client.post(
        f"/api/recipes/{recipe_id}/ad-hoc/finish",
        json={"save": True, "name": "Mystery volume"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["unconvertible"]) == 1
    assert body["unconvertible"][0]["item_name"] == "Flour"
    assert body["deducted"] == []
    assert _on_hand(client, h, loc, item_id) == Decimal("100")


def test_finish_with_nothing_deductible_still_saves_without_cook_event(client: httpx.Client) -> None:
    """Empty ledger, no location given: nothing to stamp a cook event with —
    the recipe still lands and the response says the cook wasn't logged."""
    h = _admin_headers(client)
    state = _start(client, h)
    recipe_id = state["recipe"]["id"]
    # An item that exists only in the catalog — never stocked anywhere.
    _append(client, h, recipe_id, ingredient={"name": "Saffron", "quantity": 1, "unit": "g"})

    resp = client.post(
        f"/api/recipes/{recipe_id}/ad-hoc/finish",
        json={"save": True, "name": "Imaginary paella"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["cook_logged"] is False
    assert len(body["shortfalls"]) == 1
    recipe = client.get(f"/api/recipes/{recipe_id}", headers=h).json()["recipe"]
    assert recipe["is_draft"] is False
