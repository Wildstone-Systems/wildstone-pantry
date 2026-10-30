"""Scanned OpenFoodFacts nutrition must persist to the catalog item (ADR 0011 D1).

Regression guard for the reported gap: scanning an item (e.g. 2% milk) that
resolves on OpenFoodFacts must save its nutrition to the catalog — both the raw
``nutrition`` blob and the structured per-100g macro columns — so it's available
for editing/rollups later, not just shown transiently in the scan result.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from modules.catalog.services import barcodes as barcodes_service
from modules.catalog.services.barcode_providers import ProductResult


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


_MILK = ProductResult(
    barcode="0007874200000",
    name="2% Reduced Fat Milk",
    brand="Store Brand",
    category="Dairy",
    size_description="1 gallon",
    nutrition={
        "energy-kcal_100g": 50,
        "proteins_100g": 3.4,
        "fat_100g": 2.0,
        "carbohydrates_100g": 4.8,
        "sugars_100g": 4.8,
    },
    source="open_food_facts",
)


def test_scan_autocreate_persists_nutrition_blob_and_structured(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: _MILK)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: _MILK)
    h = _admin_headers(client)

    scan = client.post("/api/barcodes/scan", json={"barcode": "0007874200000", "auto_create": True}, headers=h)
    assert scan.status_code == 200, scan.text
    body = scan.json()
    assert body["found"] is True and body["item_id"]
    item_id = body["item_id"]

    # Fetch the catalog item — nutrition must be persisted, not just in the scan result.
    item = client.get(f"/api/items/{item_id}", headers=h).json()

    # Raw OFF blob retained.
    assert item["nutrition"] is not None
    assert item["nutrition"]["proteins_100g"] == 3.4

    # Structured per-100g columns populated from the OFF keys.
    assert Decimal(str(item["energy_kcal_100g"])) == Decimal("50")
    assert Decimal(str(item["protein_g_100g"])) == Decimal("3.4")
    assert Decimal(str(item["fat_g_100g"])) == Decimal("2.0")
    assert Decimal(str(item["carbs_g_100g"])) == Decimal("4.8")


def test_rescan_backfills_structured_from_existing_blob(client: httpx.Client, monkeypatch) -> None:
    """An item that already has a nutrition blob but null structured columns (e.g.
    created before this change) gets its structured macros backfilled on re-scan —
    from the stored blob, no external call — without clobbering user-set values."""
    h = _admin_headers(client)

    # Simulate a legacy item: nutrition blob present, structured columns null,
    # and a user-set protein override we must NOT overwrite.
    created = client.post(
        "/api/items",
        json={
            "name": "Legacy Milk",
            "barcode": "0007874299999",
            "nutrition": {"energy-kcal_100g": 50, "proteins_100g": 3.4, "fat_100g": 2.0},
            "protein_g_100g": 9.9,
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    # Re-scan (local hit → backfill from blob, no external lookup needed).
    scan = client.post("/api/barcodes/scan", json={"barcode": "0007874299999", "auto_create": True}, headers=h)
    assert scan.status_code == 200, scan.text

    item = client.get(f"/api/items/{item_id}", headers=h).json()
    # Null columns filled from the blob...
    assert Decimal(str(item["energy_kcal_100g"])) == Decimal("50")
    assert Decimal(str(item["fat_g_100g"])) == Decimal("2.0")
    # ...but the user's protein override is preserved (not clobbered by the blob's 3.4).
    assert Decimal(str(item["protein_g_100g"])) == Decimal("9.9")


_RICH_PEANUT_BUTTER = ProductResult(
    barcode="0009999900000",
    name="Creamy Peanut Butter",
    brand="Acme",
    category="Pantry",
    size_description="16 oz",
    size_quantity=16.0,
    size_unit="oz",
    image_url="https://images.example/peanut-butter.jpg",
    nutrition={
        "energy-kcal_100g": 588,
        "proteins_100g": 25.0,
        "fat_100g": 50.0,
        "carbohydrates_100g": 20.0,
        "fiber_100g": 6.0,
        "sugars_100g": 9.0,
        "saturated-fat_100g": 10.0,
        "sodium_100g": 0.4,
    },
    source="open_food_facts",
    serving_size_g=32.0,
    ingredients_text="Roasted peanuts, salt.",
    allergens=["peanuts"],
    nutri_score="c",
    nova_group=3,
    eco_score="b",
)


def test_rescan_enriches_manually_created_item_with_no_gaps_from_off(
    client: httpx.Client, monkeypatch
) -> None:
    """The reported gap: a catalog item created by hand (name + barcode only)
    should get completed automatically the next time its barcode is scanned,
    using whatever Open Food Facts has — without touching anything already set."""
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: _RICH_PEANUT_BUTTER)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: _RICH_PEANUT_BUTTER)
    h = _admin_headers(client)

    created = client.post(
        "/api/items",
        json={"name": "My Peanut Butter", "barcode": "0009999900000"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    scan = client.post("/api/barcodes/scan", json={"barcode": "0009999900000", "auto_create": True}, headers=h)
    assert scan.status_code == 200, scan.text

    item = client.get(f"/api/items/{item_id}", headers=h).json()

    # The user's own name is untouched.
    assert item["name"] == "My Peanut Butter"

    # Everything that was blank got filled in from OFF.
    assert item["brand"] == "Acme"
    assert item["size_quantity"] == 16.0
    assert item["size_unit"] == "oz"
    assert Decimal(str(item["energy_kcal_100g"])) == Decimal("588")
    assert Decimal(str(item["protein_g_100g"])) == Decimal("25.0")
    assert Decimal(str(item["fiber_g_100g"])) == Decimal("6.0")
    assert Decimal(str(item["sugars_g_100g"])) == Decimal("9.0")
    assert Decimal(str(item["saturated_fat_g_100g"])) == Decimal("10.0")
    assert Decimal(str(item["sodium_g_100g"])) == Decimal("0.4")
    assert Decimal(str(item["serving_size_g"])) == Decimal("32.0")
    assert item["ingredients_text"] == "Roasted peanuts, salt."
    assert item["allergens"] == ["peanuts"]
    assert item["nutri_score"] == "c"
    assert item["nova_group"] == 3
    assert item["eco_score"] == "b"


def test_rescan_never_overwrites_a_value_already_set(client: httpx.Client, monkeypatch) -> None:
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: _RICH_PEANUT_BUTTER)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: _RICH_PEANUT_BUTTER)
    h = _admin_headers(client)

    created = client.post(
        "/api/items",
        json={"name": "House Brand Peanut Butter", "barcode": "0009999900000", "brand": "House Brand"},
        headers=h,
    )
    item_id = created.json()["id"]

    client.post("/api/barcodes/scan", json={"barcode": "0009999900000", "auto_create": True}, headers=h)

    item = client.get(f"/api/items/{item_id}", headers=h).json()
    assert item["brand"] == "House Brand"


def test_rescan_skips_external_lookup_once_already_enriched(client: httpx.Client, monkeypatch) -> None:
    """An item that already has a nutrition blob is considered enriched — a
    re-scan must not hit the external provider again."""
    calls: list[str] = []
    monkeypatch.setattr(barcodes_service, "lookup_external", lambda barcode, **_k: calls.append(barcode) or None)
    monkeypatch.setattr(barcodes_service, "lookup_external_merged", lambda barcode, **_k: calls.append(barcode) or None)
    h = _admin_headers(client)

    client.post(
        "/api/items",
        json={"name": "Already Enriched", "barcode": "0009999911111", "nutrition": {"energy-kcal_100g": 100}},
        headers=h,
    )

    scan = client.post("/api/barcodes/scan", json={"barcode": "0009999911111", "auto_create": True}, headers=h)
    assert scan.status_code == 200, scan.text
    assert calls == []
