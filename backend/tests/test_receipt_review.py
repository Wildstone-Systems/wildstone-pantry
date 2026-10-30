"""Receipt review contract: real routes, isolated SQLite, fixture OCR only."""
from uuid import uuid4

import pytest

from app.db import SessionLocal
from app.models import Inventory, InventoryTransaction, Item, Receipt, ReceiptLineItem
from modules.automation.receipt_parser import parse_receipt_text
from modules.inventory.models import InventoryEvent


@pytest.fixture
def receipt_context(client):
    credentials = {"email": "receipt-review@example.test", "password": "TestPass123!"}
    client.post("/api/auth/bootstrap-admin", json=credentials)
    login = client.post("/api/auth/login", json=credentials)
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    location = client.post("/api/locations", json={"name": "Pantry"}, headers=headers).json()["id"]
    item = client.post("/api/items", json={"name": "Milk"}, headers=headers).json()["id"]
    return headers, location, item


def _counts():
    with SessionLocal() as db:
        return tuple(db.query(model).count() for model in
                     (Item, Receipt, ReceiptLineItem, Inventory, InventoryTransaction, InventoryEvent))


def _line(**changes):
    return {"raw_line": "Milk,2,each", "name": "Milk", "quantity": "2", "unit": "each", **changes}


def test_preview_selection_adds_stock_without_duplicate_catalog_item(client, receipt_context):
    headers, location, item = receipt_context
    preview = client.post("/api/receipts/preview", headers=headers,
                          json={"location_id": location, "raw_text": "Milk,2,each"})
    assert preview.status_code == 200
    line = preview.json()["lines"][0]
    assert line["matched_item_id"] == item
    result = client.post("/api/receipts/commit", headers=headers,
                         json={"location_id": location, "lines": [line]})
    assert result.status_code == 200, result.text
    assert result.json()["lines"][0]["matched_item_id"] == item
    assert _counts() == (1, 1, 1, 1, 1, 1)
    stock = client.get(f"/api/inventory?location_id={location}", headers=headers).json()["items"]
    assert stock[0]["item_id"] == item
    assert float(stock[0]["quantity"]) == 2


def test_changed_selection_is_honored_without_renaming_selected_item(client, receipt_context):
    headers, location, _ = receipt_context
    replacement = client.post("/api/items", json={"name": "Oat milk"}, headers=headers).json()["id"]
    result = client.post("/api/receipts/commit", headers=headers, json={"location_id": location,
        "lines": [_line(name="Edited OCR name", matched_item_id=replacement)]})
    assert result.status_code == 200, result.text
    assert result.json()["lines"][0]["matched_item_id"] == replacement
    assert result.json()["lines"][0]["matched_item_name"] == "Oat milk"
    assert _counts() == (2, 1, 1, 1, 1, 1)


@pytest.mark.parametrize("inactive", [False, True])
def test_invalid_selection_rejects_entire_batch_before_writes(client, receipt_context, inactive):
    headers, location, item = receipt_context
    selected = str(uuid4())
    if inactive:
        assert client.delete(f"/api/items/{item}", headers=headers).status_code in (200, 204)
        selected = item
    before = _counts()
    result = client.post("/api/receipts/commit", headers=headers, json={"location_id": location,
        "lines": [_line(name="New food", create_new=True), _line(matched_item_id=selected)]})
    assert result.status_code == 404, result.text
    assert _counts() == before


@pytest.mark.parametrize("changes", [
    {}, {"create_new": True, "matched_item_id": str(uuid4())},
    {"create_new": True, "quantity": "0"}, {"create_new": True, "quantity": "-1"},
    {"create_new": True, "quantity": "NaN"}, {"create_new": True, "quantity": "Infinity"},
    {"create_new": True, "quantity": "bad"}, {"create_new": True, "quantity": "0.0001"},
    {"create_new": True, "quantity": "1000000000"}, {"create_new": True, "unit": "unknown"},
    {"create_new": True, "name": "   "}, {"matched_item_id": "not-a-uuid"},
])
def test_invalid_review_inputs_never_write(client, receipt_context, changes):
    headers, location, _ = receipt_context
    before = _counts()
    response = client.post("/api/receipts/commit", headers=headers,
                           json={"location_id": location, "lines": [_line(**changes)]})
    assert response.status_code == 422, response.text
    assert _counts() == before


def test_explicit_create_new_and_unit_alias_are_supported(client, receipt_context):
    headers, location, _ = receipt_context
    response = client.post("/api/receipts/commit", headers=headers, json={"location_id": location,
        "lines": [_line(name=" New food ", create_new=True, unit=" EA ")]})
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]
    assert line["matched_item_name"] == "New food"
    assert line["parsed_unit"] == "each"
    assert _counts() == (2, 1, 1, 1, 1, 1)


@pytest.mark.parametrize("photo", [False, True])
def test_text_and_photo_previews_do_not_write_any_receipt_or_stock(client, receipt_context, monkeypatch, photo):
    headers, location, _ = receipt_context
    before = _counts()
    if photo:
        monkeypatch.setattr("modules.automation.image_bytes_to_text", lambda image: "Milk,2,each\nTAX 1.00")
        response = client.post(f"/api/receipts/preview-image?location_id={location}", headers=headers,
                               files={"file": ("receipt.jpg", b"fixture", "image/jpeg")})
    else:
        response = client.post("/api/receipts/preview", headers=headers,
                               json={"location_id": location, "raw_text": "Milk,2,each\nTAX 1.00"})
    assert response.status_code == 200, response.text
    assert response.json()["line_count"] == 1
    assert _counts() == before


def test_receipt_summary_rows_and_unit_boundaries():
    lines = parse_receipt_text("TOTAL 12.34\nTAX 1.00\nCOUPON 2.00\nSUBTOTAL: $9.00\n"
                               "Basil\nBagel\nTotal cereal 1 each\nRice 2kg\nMilk 2 each")
    assert [(line["name"], str(line["quantity"]), line["unit"]) for line in lines] == [
        ("Basil", "1", "each"), ("Bagel", "1", "each"), ("Total cereal", "1", "each"),
        ("Rice", "2", "kg"), ("Milk", "2", "each"),
    ]


def test_variable_weight_count_rejected_before_any_batch_writes(client, receipt_context):
    headers, location, _ = receipt_context
    item = client.post("/api/items", headers=headers,
                       json={"name": "Roast", "variable_weight": True}).json()["id"]
    before = _counts()
    response = client.post("/api/receipts/commit", headers=headers, json={"location_id": location,
        "lines": [_line(name="New food", create_new=True), _line(matched_item_id=item)]})
    assert response.status_code == 422, response.text
    assert _counts() == before


def test_unavailable_photo_extraction_preserves_inventory(client, receipt_context, monkeypatch):
    headers, location, _ = receipt_context
    def unavailable(image):
        raise RuntimeError("Fixture OCR unavailable")
    monkeypatch.setattr("modules.automation.image_bytes_to_text", unavailable)
    monkeypatch.setattr("modules.automation.providers.resolve_provider", lambda db, cap: None)
    before = _counts()
    response = client.post(f"/api/receipts/preview-image?location_id={location}", headers=headers,
                           files={"file": ("receipt.jpg", b"fixture", "image/jpeg")})
    assert response.status_code == 503
    assert _counts() == before


def test_legacy_direct_ingest_still_matches_existing_item(client, receipt_context):
    headers, location, item = receipt_context
    response = client.post("/api/receipts/ingest", headers=headers,
                           json={"location_id": location, "raw_text": "Milk,2,each\nTOTAL 3.00"})
    assert response.status_code == 200, response.text
    assert response.json()["lines"][0]["matched_item_id"] == item
    assert _counts() == (1, 1, 1, 1, 1, 1)
