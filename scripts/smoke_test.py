#!/usr/bin/env python3
from __future__ import annotations

import os
import uuid

import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


def log(step: str, detail: str) -> None:
    print(f"[{step}] {detail}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    # Barcodes are normalized to digits by the API, so generate digits-only here.
    barcode = f"{int(uuid.uuid4()) % 10**12:012d}"
    location_a = f"Smoke-Pantry-{uuid.uuid4().hex[:6]}"
    location_b = f"Smoke-Fridge-{uuid.uuid4().hex[:6]}"
    # Use stable defaults so the smoke test works when users already exist.
    # Override via env vars if you bootstrapped with different credentials.
    admin_email = os.getenv("SMOKE_ADMIN_EMAIL", "admin@pantryos.local")
    admin_password = os.getenv("SMOKE_ADMIN_PASSWORD", "TestPass123!")

    log("1/20", f"Calling /health on {BASE_URL}")
    health_res = requests.get(f"{BASE_URL}/health", timeout=10)
    require(health_res.status_code == 200, f"Health check failed: {health_res.status_code}")
    health_payload = health_res.json()
    require(health_payload.get("status") == "ok", "Unexpected health status")
    require(health_payload.get("database") == "connected", "Database is not connected")

    log("2/20", "Bootstrapping admin user")
    bootstrap_res = requests.post(
        f"{BASE_URL}/api/auth/bootstrap-admin",
        json={"email": admin_email, "password": admin_password},
        timeout=10,
    )
    require(bootstrap_res.status_code in {200, 409}, f"Bootstrap failed: {bootstrap_res.status_code} {bootstrap_res.text}")

    log("3/20", "Logging in")
    # Personal mode: if auth is disabled, /api/auth/me works without a bearer token.
    me_probe = requests.get(f"{BASE_URL}/api/auth/me", timeout=10)
    if me_probe.status_code == 200:
        headers = {}
    else:
        login_res = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": admin_email, "password": admin_password},
            timeout=10,
        )
        require(login_res.status_code == 200, f"Login failed: {login_res.status_code} {login_res.text}")
        token = login_res.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

    log("4/20", "Creating item")
    create_res = requests.post(
        f"{BASE_URL}/api/items",
        json={
            "name": "Smoke Test Item",
            "brand": "PantryOS",
            "barcode": barcode,
            "default_expiration_days": 14,
        },
        headers=headers,
        timeout=10,
    )
    require(create_res.status_code == 201, f"Create failed: {create_res.status_code} {create_res.text}")
    created = create_res.json()
    item_id = created["id"]

    log("5/20", "Fetching item")
    fetch_res = requests.get(f"{BASE_URL}/api/items/{item_id}", headers=headers, timeout=10)
    require(fetch_res.status_code == 200, f"Fetch failed: {fetch_res.status_code}")
    fetched = fetch_res.json()
    require(fetched["barcode"] == barcode, "Fetched item barcode mismatch")

    log("6/20", "Updating item")
    update_res = requests.put(
        f"{BASE_URL}/api/items/{item_id}",
        json={"name": "Smoke Test Item Updated", "default_expiration_days": 21},
        headers=headers,
        timeout=10,
    )
    require(update_res.status_code == 200, f"Update failed: {update_res.status_code} {update_res.text}")

    log("7/20", "Creating two locations")
    loc_a_res = requests.post(f"{BASE_URL}/api/locations", json={"name": location_a}, headers=headers, timeout=10)
    loc_b_res = requests.post(f"{BASE_URL}/api/locations", json={"name": location_b}, headers=headers, timeout=10)
    require(loc_a_res.status_code == 201, f"Location A create failed: {loc_a_res.status_code}")
    require(loc_b_res.status_code == 201, f"Location B create failed: {loc_b_res.status_code}")
    loc_a_id = loc_a_res.json()["id"]
    loc_b_id = loc_b_res.json()["id"]

    log("8/20", "Adding inventory")
    add_res = requests.post(
        f"{BASE_URL}/api/inventory/add",
        json={"item_id": item_id, "location_id": loc_a_id, "quantity": 5, "unit": "each"},
        headers=headers,
        timeout=10,
    )
    require(add_res.status_code == 200, f"Add inventory failed: {add_res.status_code} {add_res.text}")

    log("9/20", "Moving inventory between locations")
    move_res = requests.post(
        f"{BASE_URL}/api/inventory/move",
        json={
            "item_id": item_id,
            "source_location_id": loc_a_id,
            "target_location_id": loc_b_id,
            "quantity": 2,
            "unit": "each",
        },
        headers=headers,
        timeout=10,
    )
    require(move_res.status_code == 200, f"Move inventory failed: {move_res.status_code} {move_res.text}")

    log("10/20", "Checking inventory and transactions")
    inv_a = requests.get(f"{BASE_URL}/api/inventory?location_id={loc_a_id}", headers=headers, timeout=10)
    tx_res = requests.get(f"{BASE_URL}/api/inventory/transactions?limit=10", headers=headers, timeout=10)
    require(inv_a.status_code == 200, "Inventory read failed")
    require(tx_res.status_code == 200, "Transaction read failed")

    log("11/20", "Checking barcode lookup")
    barcode_lookup = requests.get(f"{BASE_URL}/api/barcodes/lookup/{barcode}", headers=headers, timeout=10)
    require(barcode_lookup.status_code == 200, "Barcode lookup failed")

    log("12/20", "Ingesting receipt text")
    receipt_res = requests.post(
        f"{BASE_URL}/api/receipts/ingest",
        json={"location_id": loc_a_id, "raw_text": "smoke apples,2,each\\nsmoke bread,1,each", "source_filename": "smoke.txt"},
        headers=headers,
        timeout=15,
    )
    require(receipt_res.status_code == 200, f"Receipt ingest failed: {receipt_res.status_code}")

    log("13/20", "Creating recipe and ingredients")
    recipe_res = requests.post(
        f"{BASE_URL}/api/recipes",
        json={"name": "Smoke Recipe", "instructions": "Cook smoke item"},
        headers=headers,
        timeout=10,
    )
    require(recipe_res.status_code == 200, f"Recipe create failed: {recipe_res.status_code}")
    recipe_id = recipe_res.json()["id"]
    recipe_ing_res = requests.put(
        f"{BASE_URL}/api/recipes/{recipe_id}/ingredients",
        json=[{"item_id": item_id, "quantity_required": 1, "unit": "each"}],
        headers=headers,
        timeout=10,
    )
    require(recipe_ing_res.status_code == 200, "Recipe ingredient set failed")

    log("14/20", "Cooking recipe")
    cook_res = requests.post(
        f"{BASE_URL}/api/recipes/{recipe_id}/cook",
        json={"location_id": loc_a_id, "servings": 1, "leftover_servings": 0},
        headers=headers,
        timeout=10,
    )
    require(cook_res.status_code == 200, "Recipe cook failed")

    log("15/20", "Checking recipe suggestions")
    sugg_res = requests.get(f"{BASE_URL}/api/recipes/suggestions/{loc_a_id}", headers=headers, timeout=10)
    require(sugg_res.status_code == 200, "Recipe suggestions failed")

    log("16/20", "Loading AI insight endpoints")
    trends_res = requests.get(f"{BASE_URL}/api/insights/trends/{loc_a_id}", headers=headers, timeout=10)
    restock_res = requests.get(f"{BASE_URL}/api/insights/restock/{loc_a_id}", headers=headers, timeout=10)
    expiring_res = requests.get(f"{BASE_URL}/api/insights/expiring/{loc_a_id}?within_days=365", headers=headers, timeout=10)
    require(trends_res.status_code == 200, "Trend insights failed")
    require(restock_res.status_code == 200, "Restock insights failed")
    require(expiring_res.status_code == 200, "Expiring insights failed")

    log("17/20", "Checking auth me endpoint")
    me_res = requests.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=10)
    require(me_res.status_code == 200, "Auth me failed")

    log("18/20", "Checking entitlement endpoint")
    entitlement_res = requests.get(f"{BASE_URL}/api/entitlement", timeout=10)
    require(entitlement_res.status_code == 200, "Entitlement read failed")
    require(entitlement_res.json().get("mode") == "free", "Expected free mode in smoke stack")

    log("19/20", "Archiving item while preserving catalog identity and transaction history")
    history_url = f"{BASE_URL}/api/inventory/transactions?location_id={loc_a_id}&limit=500"
    history_before = requests.get(history_url, headers=headers, timeout=10)
    require(history_before.status_code == 200, "Pre-archive transaction read failed")
    transaction_ids = {
        tx["transaction_id"] for tx in history_before.json()["transactions"] if tx["item_id"] == item_id
    }
    require(bool(transaction_ids), "Smoke item should have transaction history before archive")
    delete_res = requests.delete(f"{BASE_URL}/api/items/{item_id}", headers=headers, timeout=10)
    require(delete_res.status_code == 200, f"Archive failed: {delete_res.status_code} {delete_res.text}")
    archived = delete_res.json()
    require(archived["id"] == item_id and archived["is_active"] is False, "Archive must retain an inactive item")
    post_delete_fetch = requests.get(f"{BASE_URL}/api/items/{item_id}", headers=headers, timeout=10)
    require(post_delete_fetch.status_code == 200, "Archived item should remain addressable")
    require(post_delete_fetch.json()["id"] == item_id and post_delete_fetch.json()["is_active"] is False,
            "Fetched archived item identity/status mismatch")
    active_items = requests.get(f"{BASE_URL}/api/items", headers=headers, timeout=10)
    require(active_items.status_code == 200, "Active catalog read failed")
    require(all(item["id"] != item_id for item in active_items.json()["items"]),
            "Archived item must be excluded from active catalog")
    all_items = requests.get(f"{BASE_URL}/api/items?include_inactive=true", headers=headers, timeout=10)
    require(all_items.status_code == 200, "Full catalog read failed")
    require(any(item["id"] == item_id and item["is_active"] is False for item in all_items.json()["items"]),
            "Full catalog must retain archived item")
    history_after = requests.get(history_url, headers=headers, timeout=10)
    require(history_after.status_code == 200, "Post-archive transaction read failed")
    remaining_ids = {tx["transaction_id"] for tx in history_after.json()["transactions"] if tx["item_id"] == item_id}
    require(transaction_ids <= remaining_ids, "Archiving must preserve the item's transaction history")

    log("20/20", "Smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1) from exc
