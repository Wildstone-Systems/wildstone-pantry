"""Retailer capabilities + search-handoff sessions + product maps (ADR 0019).

The Phase-1 loop end to end: retailers are capability declarations (data rows
for search-link retailers, code overlays for Kroger/Instacart), the handoff
session snapshots the buy list into deep links, the post-shop confirmation
closes list entries into an idempotent Purchase, and confirmed picks teach
``retailer_product_maps`` so the next trip opens the product page directly.
"""

from __future__ import annotations

import httpx


def _headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "handoff@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "handoff@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _location_id(client: httpx.Client, h: dict[str, str]) -> str:
    resp = client.post("/api/locations", json={"name": "Pantry"}, headers=h)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_retailers_declare_vs_available(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.get("/api/retailers", headers=h)
    assert resp.status_code == 200, resp.text
    retailers = {r["key"]: r for r in resp.json()["retailers"]}

    # The full matrix ships as seed rows.
    for key in ("walmart", "target", "heb", "kroger", "instacart", "bestbuy", "ebay"):
        assert key in retailers

    # A search-link retailer: declared == available (no credentials needed).
    walmart = retailers["walmart"]
    assert "search_deep_link" in walmart["capabilities"]
    assert "search_deep_link" in walmart["available"]

    # Kroger DECLARES cart_add/product_search but they are not AVAILABLE
    # without credentials or a brokered connection — the declared≠available
    # distinction the single configured() boolean couldn't express.
    kroger = retailers["kroger"]
    assert "cart_add" in kroger["capabilities"]
    assert "cart_add" not in kroger["available"]
    assert "search_deep_link" in kroger["available"]

    # Instacart declares cart_link, dormant without an API key.
    instacart = retailers["instacart"]
    assert "cart_link" in instacart["capabilities"]
    assert "cart_link" not in instacart["available"]


def test_handoff_session_end_to_end(client: httpx.Client) -> None:
    h = _headers(client)
    location_id = _location_id(client, h)

    # Snapshot needs a pending list.
    assert client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).status_code == 400

    client.post("/api/shopping-list", json={"name": "Whole Milk", "quantity": 1, "unit": "each"}, headers=h)
    client.post("/api/shopping-list", json={"name": "Eggs", "quantity": 1, "unit": "each"}, headers=h)

    resp = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h)
    assert resp.status_code == 201, resp.text
    session = resp.json()
    assert session["retailer_label"] == "Walmart"
    assert session["status"] == "active"
    assert len(session["items"]) == 2
    milk = next(i for i in session["items"] if i["name"] == "Whole Milk")
    assert "walmart.com/search" in milk["search_url"]
    assert "Whole+Milk" in milk["search_url"]

    # Session survives as a row and is resumable.
    active = client.get("/api/shopping-list/handoff", headers=h).json()["sessions"]
    assert [s["id"] for s in active] == [session["id"]]

    # Work the store site: milk found (with a store identity), eggs unavailable.
    upd = client.patch(
        f"/api/shopping-list/handoff/{session['id']}/items/{milk['id']}",
        json={"state": "added", "external_id": "WM-1234", "external_name": "Great Value Whole Milk, 1 Gallon"},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["state"] == "added"
    eggs = next(i for i in session["items"] if i["name"] == "Eggs")
    client.patch(
        f"/api/shopping-list/handoff/{session['id']}/items/{eggs['id']}",
        json={"state": "unavailable"},
        headers=h,
    )

    # Post-shop confirmation: only the milk was purchased.
    done = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [milk["id"]], "location_id": location_id},
        headers=h,
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "completed"
    assert body["purchased_count"] == 1
    assert body["purchase_id"] is not None

    # The buy list closed the loop: milk purchased, eggs still pending.
    entries = {e["name"]: e for e in client.get("/api/shopping-list", headers=h).json()["items"]}
    assert entries["Whole Milk"]["status"] == "purchased"
    assert entries["Eggs"]["status"] == "pending"

    # The Purchase is real and idempotent (same session can't record twice).
    detail = client.get(f"/api/purchases/{body['purchase_id']}", headers=h).json()
    assert detail["purchase"]["source"] == "handoff:walmart"
    assert detail["purchase"]["merchant"] == "Walmart"
    assert len(detail["lines"]) == 1
    assert detail["lines"][0]["parsed_name"] == "Great Value Whole Milk, 1 Gallon"
    again = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [milk["id"]]},
        headers=h,
    )
    assert again.status_code == 400  # session no longer active


def test_confirmed_pick_teaches_the_map_for_the_next_trip(client: httpx.Client) -> None:
    h = _headers(client)

    # Trip 1: a linked catalog item, confirmed at Walmart with a product URL.
    item = client.post("/api/items", json={"name": "Peanut Butter"}, headers=h).json()
    client.post(
        "/api/shopping-list",
        json={"name": "Peanut Butter", "item_id": item["id"], "quantity": 1, "unit": "each"},
        headers=h,
    )
    session = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row = session["items"][0]
    assert "walmart.com/search" in row["search_url"]  # no mapping yet → name search

    client.patch(
        f"/api/shopping-list/handoff/{session['id']}/items/{row['id']}",
        json={
            "state": "added",
            "external_id": "WM-PB-42",
            "external_name": "Jif Creamy Peanut Butter 16oz",
            "external_url": "https://www.walmart.com/ip/Jif-Creamy/42",
        },
        headers=h,
    )
    client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [row["id"]]},
        headers=h,
    )

    # Trip 2: the same need now deep-links straight to the confirmed product.
    client.post(
        "/api/shopping-list",
        json={"name": "Peanut Butter", "item_id": item["id"], "quantity": 1, "unit": "each"},
        headers=h,
    )
    session2 = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row2 = session2["items"][0]
    assert row2["search_url"] == "https://www.walmart.com/ip/Jif-Creamy/42"
    assert row2["search_query"] == "Jif Creamy Peanut Butter 16oz"


def test_offer_pick_writes_a_mapping(client: httpx.Client) -> None:
    h = _headers(client)
    resp = client.post(
        "/api/shopping-list/from-offer",
        json={
            "provider": "kroger",
            "name": "Kroger 2% Milk",
            "vendor_sku": "0001111041700",
            "upc": "0001111041700",
            "quantity": 1,
            "unit": "each",
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["item_id"] is not None

    from app.db.session import SessionLocal
    from app.models import RetailerProductMap
    from sqlalchemy import select

    with SessionLocal() as db:
        row = db.execute(select(RetailerProductMap)).scalars().one()
        assert str(row.item_id) == entry["item_id"]
        assert row.retailer_key == "kroger"
        assert row.external_id == "0001111041700"
        assert row.source == "offer_pick"


def test_item_locations_override_trip_level_location(client: httpx.Client) -> None:
    """Household feedback on ADR 0019: beef patties go in the fridge, kidney
    beans in the pantry — a single trip-level location select is wrong."""
    h = _headers(client)
    pantry_id = _location_id(client, h)
    fridge = client.post("/api/locations", json={"name": "Fridge"}, headers=h)
    assert fridge.status_code in (200, 201), fridge.text
    fridge_id = fridge.json()["id"]

    patty_item = client.post("/api/items", json={"name": "Beef Patties"}, headers=h).json()
    bean_item = client.post("/api/items", json={"name": "Kidney Beans"}, headers=h).json()
    client.post(
        "/api/shopping-list",
        json={"name": "Beef Patties", "item_id": patty_item["id"], "quantity": 1, "unit": "each"},
        headers=h,
    )
    client.post(
        "/api/shopping-list",
        json={"name": "Kidney Beans", "item_id": bean_item["id"], "quantity": 2, "unit": "each"},
        headers=h,
    )

    resp = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h)
    assert resp.status_code == 201, resp.text
    session = resp.json()
    patties = next(i for i in session["items"] if i["name"] == "Beef Patties")
    beans = next(i for i in session["items"] if i["name"] == "Kidney Beans")

    for row in (patties, beans):
        client.patch(
            f"/api/shopping-list/handoff/{session['id']}/items/{row['id']}",
            json={"state": "added"},
            headers=h,
        )

    # Trip-level location_id defaults to Pantry; per-item override sends the
    # patties to the Fridge instead.
    done = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={
            "purchased_item_ids": [patties["id"], beans["id"]],
            "location_id": pantry_id,
            "item_locations": {patties["id"]: fridge_id},
        },
        headers=h,
    )
    assert done.status_code == 200, done.text

    fridge_items = {
        row["item_name"] for row in client.get("/api/inventory", params={"location_id": fridge_id}, headers=h).json()["items"]
    }
    pantry_items = {
        row["item_name"] for row in client.get("/api/inventory", params={"location_id": pantry_id}, headers=h).json()["items"]
    }
    assert "Beef Patties" in fridge_items
    assert "Beef Patties" not in pantry_items
    # Kidney Beans had no override — falls back to the trip-level location.
    assert "Kidney Beans" in pantry_items
    assert "Kidney Beans" not in fridge_items


def test_item_locations_unknown_location_is_404(client: httpx.Client) -> None:
    h = _headers(client)
    client.post("/api/shopping-list", json={"name": "Tofu", "quantity": 1, "unit": "each"}, headers=h)
    session = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row = session["items"][0]
    client.patch(
        f"/api/shopping-list/handoff/{session['id']}/items/{row['id']}",
        json={"state": "added"},
        headers=h,
    )
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [row["id"]], "item_locations": {row["id"]: bogus}},
        headers=h,
    )
    assert resp.status_code == 404, resp.text


def test_suggested_location_id_reflects_prior_trip(client: httpx.Client) -> None:
    h = _headers(client)
    fridge = client.post("/api/locations", json={"name": "Fridge2"}, headers=h)
    assert fridge.status_code in (200, 201), fridge.text
    fridge_id = fridge.json()["id"]

    item = client.post("/api/items", json={"name": "Ground Turkey"}, headers=h).json()
    client.post(
        "/api/shopping-list",
        json={"name": "Ground Turkey", "item_id": item["id"], "quantity": 1, "unit": "each"},
        headers=h,
    )
    session = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row = session["items"][0]
    # Never-stocked item: no suggestion yet.
    assert row["suggested_location_id"] is None

    client.patch(
        f"/api/shopping-list/handoff/{session['id']}/items/{row['id']}",
        json={"state": "added"},
        headers=h,
    )
    done = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [row["id"]], "location_id": fridge_id},
        headers=h,
    )
    assert done.status_code == 200, done.text

    # Second trip: the item now suggests the location it landed in last time.
    client.post(
        "/api/shopping-list",
        json={"name": "Ground Turkey", "item_id": item["id"], "quantity": 1, "unit": "each"},
        headers=h,
    )
    session2 = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row2 = session2["items"][0]
    assert row2["suggested_location_id"] == fridge_id


def test_unknown_retailer_and_broken_template_fallback(client: httpx.Client) -> None:
    h = _headers(client)
    client.post("/api/shopping-list", json={"name": "Bread", "quantity": 1}, headers=h)
    assert client.post("/api/shopping-list/handoff", json={"retailer": "nope"}, headers=h).status_code == 404

    # A template marked broken degrades to the site-search fallback — the
    # handoff never dead-ends on a stale URL (403s never set this; only
    # 404/redirect-home do).
    from app.db.session import SessionLocal
    from app.models import RetailerLink
    from sqlalchemy import select

    client.get("/api/retailers", headers=h)  # ensure seed
    with SessionLocal() as db:
        target = db.execute(select(RetailerLink).where(RetailerLink.key == "target")).scalar_one()
        target.status = "broken"
        db.add(target)
        db.commit()

    session = client.post("/api/shopping-list/handoff", json={"retailer": "target"}, headers=h).json()
    url = session["items"][0]["search_url"]
    assert url.startswith("https://duckduckgo.com/?q=site%3Atarget.com")


def test_complete_rejects_stray_item_location_keys(client: httpx.Client) -> None:
    """A stale/mistyped item_locations key must fail loudly (404), not silently
    skip the inventory intake it was meant to direct."""
    h = _headers(client)
    client.post("/api/shopping-list", json={"name": "Yogurt", "quantity": 1}, headers=h)
    session = client.post("/api/shopping-list/handoff", json={"retailer": "walmart"}, headers=h).json()
    row = session["items"][0]
    location_id = _location_id(client, h)

    resp = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={
            "purchased_item_ids": [row["id"]],
            "item_locations": {"00000000-0000-0000-0000-000000000000": location_id},
        },
        headers=h,
    )
    assert resp.status_code == 404, resp.text
    assert "item_locations" in resp.json()["detail"]

    # The session is still active and completable after the failed call.
    ok = client.post(
        f"/api/shopping-list/handoff/{session['id']}/complete",
        json={"purchased_item_ids": [row["id"]], "item_locations": {row["id"]: location_id}},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
