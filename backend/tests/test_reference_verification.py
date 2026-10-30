"""Candidates are never nutrition until their reference is verified."""
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Item
from modules.automation import food_judge
from modules.catalog.services import barcodes, enrichment
from modules.catalog.services.barcode_providers import NutritionEstimate, ProductResult


def test_local_scan_without_nutrition_does_not_lock_before_provider_lookup(client, monkeypatch):
    with SessionLocal() as db:
        item = Item(name="No nutrition yet", barcode="123456789012", nutrition=None)
        db.add(item)
        db.commit()

    original_refresh = Session.refresh

    def refresh_without_backfill_lock(session, instance, *args, **kwargs):
        assert kwargs.get("with_for_update") is not True
        return original_refresh(session, instance, *args, **kwargs)

    monkeypatch.setattr(Session, "refresh", refresh_without_backfill_lock)
    monkeypatch.setattr(barcodes, "lookup_external", lambda *args, **kwargs: None)

    response = client.post("/api/barcodes/scan", json={"barcode": "123456789012", "auto_create": True})
    assert response.status_code == 200, response.text
    assert response.json()["found"] is True


@pytest.mark.parametrize("path", ["create", "rescan", "reconcile", "enrich"])
@pytest.mark.parametrize("outcome", ["rejected", "unverified", "verified", "exception"])
def test_all_automatic_paths_verify_before_any_write(client, monkeypatch, path, outcome):
    candidate = NutritionEstimate({"energy-kcal_100g": 23, "proteins_100g": 1.2}, "Tomatillos, raw", 168566)
    monkeypatch.setattr(barcodes, "estimate_nutrition_by_name", lambda *a, **k: candidate)
    product = ProductResult(barcode="123456789012", name="Tomatillos", source="fixture")
    monkeypatch.setattr(barcodes, "lookup_external_merged", lambda *a, **k: product)
    observed = []

    def judge(db, **kwargs):
        with SessionLocal() as observer:
            stored = observer.execute(select(Item).where(Item.name == "Tomatillos")).scalar_one()
            observed.append((stored.nutrition, stored.energy_kcal_100g, stored.protein_g_100g,
                             stored.nutrition_estimated, stored.nutrition_source))
        if outcome == "exception":
            raise RuntimeError("fixture outage")
        return food_judge.FoodVerdict(outcome == "verified", "fixture", outcome != "unverified")

    monkeypatch.setattr(food_judge, "same_food", judge)
    with SessionLocal() as db:
        if path == "create":
            item = barcodes._create_item_from_result(db, product)
        else:
            item_id = client.post("/api/items", json={"name": "Tomatillos"}).json()["id"]
            item = db.get(Item, UUID(item_id))
            if path == "rescan":
                response = client.post(f"/api/items/{item_id}/rescan")
                assert response.status_code == 200
                assert response.json()["reference_review"]["status"] == (
                    "unavailable" if outcome == "exception" else outcome)
            elif path == "reconcile":
                barcodes._reconcile_item_from_result(db, item, product)
            else:
                enrichment.enrich_item(db, item, with_density=False)
            db.refresh(item)
        assert observed == [(None, None, None, False, None)]
        if outcome == "verified":
            assert item.nutrition["energy-kcal_100g"] == 23
            assert item.energy_kcal_100g == Decimal("23")
            assert item.nutrition["_reference_verification"]["status"] == "verified"
        else:
            assert item.nutrition is None
            assert item.energy_kcal_100g is None
            assert item.protein_g_100g is None
            assert item.nutrition_estimated is False
            assert item.nutrition_source is None


@pytest.mark.parametrize("values", [
    {"nutrition": {"proteins_100g": 4}}, {"protein_g_100g": 4}, {"energy_kcal_100g": 0},
])
def test_partial_existing_authority_never_gets_automatic_reference(client, monkeypatch, values):
    calls = []
    monkeypatch.setattr(barcodes, "estimate_nutrition_by_name", lambda *a, **k: calls.append(a))
    row = client.post("/api/items", json={"name": "Partial label", **values}).json()
    got = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    assert not calls
    assert got["nutrition"] == row["nutrition"]
    assert got["protein_g_100g"] == row["protein_g_100g"]
    assert got["nutrition_estimated"] is False


def test_human_reference_replaces_managed_macros_preserving_other_keys(client, monkeypatch):
    monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": 20}, "Chosen reference", 123))
    row = client.post("/api/items", json={"name": "Food", "nutrition": {
        "energy-kcal_100g": 90, "proteins_100g": 8, "serving_note": "one spoon"},
        "energy_kcal_100g": 90, "protein_g_100g": 8}).json()
    result = client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123}).json()
    assert result["energy_kcal_100g"] == 20
    assert result["protein_g_100g"] is None
    assert "proteins_100g" not in result["nutrition"]
    assert result["nutrition"]["serving_note"] == "one spoon"
    assert result["nutrition_reference"]["status"] == "human_selected"
    assert result["nutrition_reference"]["fdc_id"] == 123
    assert client.post(f"/api/items/{row['id']}/rescan").json()["item"] == result


def test_candidates_are_unverified_and_read_only(client, monkeypatch):
    monkeypatch.setattr(barcodes, "search_nutrition_candidates", lambda *a, **k: [NutritionEstimate(
        {"energy-kcal_100g": 20}, "Candidate", 123)])
    row = client.post("/api/items", json={"name": "Food"}).json()
    response = client.get(f"/api/items/{row['id']}/nutrition-candidates").json()
    assert response["candidates"][0]["verification_status"] == "unverified"
    assert response["candidates"][0]["source"] == "usda_fdc"
    assert client.get(f"/api/items/{row['id']}").json() == row


def test_label_reference_provenance(client, monkeypatch):
    monkeypatch.setattr(barcodes, "lookup_external_merged", lambda *a, **k: ProductResult(
        "123456789012", "Label food", nutrition={"energy-kcal_100g": 90}, source="open_food_facts"))
    row = client.post("/api/items", json={"name": "Food", "barcode": "123456789012"}).json()
    result = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    assert result["nutrition_reference"]["status"] == "label_exact"
    assert result["nutrition_reference"]["source"] == "open_food_facts"
    assert result["nutrition_estimated"] is False


@pytest.mark.parametrize("status", ["verified", "label_exact", "human_selected"])
def test_client_json_cannot_assert_server_owned_reference_authority(client, status):
    spoof = {"_reference_verification": {"status": status, "source": "spoof"}}
    created = client.post("/api/items", json={"name": "Food", "nutrition": spoof}).json()
    assert created["nutrition_reference"]["status"] == "legacy_unknown"
    assert created["nutrition"] is None
    edited = client.put(f"/api/items/{created['id']}", json={"nutrition": {
        **spoof, "proteins_100g": 3}})
    assert edited.status_code == 200, edited.text
    assert edited.json()["nutrition_reference"]["status"] == "manual"
    assert edited.json()["protein_g_100g"] == 3


def test_manual_edit_invalidates_reference_and_synchronizes_representations(client, monkeypatch):
    monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": 20, "proteins_100g": 2}, "Chosen", 123))
    row = client.post("/api/items", json={"name": "Food"}).json()
    chosen = client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123}).json()
    unchanged = client.put(f"/api/items/{row['id']}", json={"protein_g_100g": 2}).json()
    assert unchanged["nutrition_reference"] == chosen["nutrition_reference"]
    changed = client.put(f"/api/items/{row['id']}", json={"protein_g_100g": 4}).json()
    assert changed["nutrition_reference"]["status"] == "manual"
    assert changed["nutrition_estimated"] is False
    assert changed["nutrition"]["proteins_100g"] == changed["protein_g_100g"] == 4
    assert changed["nutrition"]["energy-kcal_100g"] == changed["energy_kcal_100g"] == 20


def test_human_write_during_verification_wins(client, monkeypatch):
    row = client.post("/api/items", json={"name": "Food"}).json()
    monkeypatch.setattr(barcodes, "estimate_nutrition_by_name", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": 23}, "Automatic candidate", 123))

    def judge(*args, **kwargs):
        response = client.put(f"/api/items/{row['id']}", json={"energy_kcal_100g": 90})
        assert response.status_code == 200
        return food_judge.FoodVerdict(True, "fixture")

    monkeypatch.setattr(food_judge, "same_food", judge)
    response = client.post(f"/api/items/{row['id']}/rescan").json()
    assert response["item"]["energy_kcal_100g"] == 90
    assert response["item"]["nutrition"]["energy-kcal_100g"] == 90
    assert response["item"]["nutrition_reference"]["status"] == "manual"
    assert response["estimated"] is False


def test_human_density_write_during_lookup_wins(client, monkeypatch):
    from modules.catalog.services.conversions import DensitySuggestion
    row = client.post("/api/items", json={"name": "Food"}).json()

    def suggestion(*args, **kwargs):
        response = client.put(f"/api/items/{row['id']}", json={"density_g_per_ml": 1.5})
        assert response.status_code == 200
        return DensitySuggestion(Decimal("0.5"), "Automatic candidate", 123, verified=True)

    monkeypatch.setattr("modules.catalog.services.conversions.suggest_density", suggestion)
    with SessionLocal() as db:
        item = db.get(Item, UUID(row["id"]))
        result = enrichment.enrich_item(db, item)
        assert item.density_g_per_ml == Decimal("1.5")
        assert "density" not in result


def test_human_nutrition_during_barcode_lookup_wins(client, monkeypatch):
    row = client.post("/api/items", json={"name": "Food", "barcode": "123456789012"}).json()

    def lookup(*args, **kwargs):
        assert client.put(f"/api/items/{row['id']}", json={"energy_kcal_100g": 90}).status_code == 200
        return ProductResult("123456789012", "Label food", nutrition={"energy-kcal_100g": 23}, source="fixture")

    monkeypatch.setattr(barcodes, "lookup_external_merged", lookup)
    response = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    assert response["energy_kcal_100g"] == response["nutrition"]["energy-kcal_100g"] == 90
    assert response["nutrition_reference"]["status"] == "manual"


def test_barcode_rescan_does_not_blend_into_human_chosen_reference(client, monkeypatch):
    monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": 20}, "Chosen", 123))
    monkeypatch.setattr(barcodes, "lookup_external_merged", lambda *a, **k: ProductResult(
        "123456789012", "Label food", nutrition={"proteins_100g": 99}, source="fixture"))
    row = client.post("/api/items", json={"name": "Food", "barcode": "123456789012"}).json()
    client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123})
    response = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    assert response["nutrition_reference"]["status"] == "human_selected"
    assert response["protein_g_100g"] is None
    assert "proteins_100g" not in response["nutrition"]


@pytest.mark.parametrize("status,estimated,allowed", [
    (None, True, False), ("unverified", True, False), ("rejected", True, False),
    ("verified", True, True), ("human_selected", True, True),
    ("manual", False, True), ("label_exact", False, True), (None, False, True),
])
def test_local_barcode_backfill_respects_stored_reference_authority(client, status, estimated, allowed):
    # Direct storage represents legacy data; public create/update strips claims.
    nutrition = {"energy-kcal_100g": 90, "proteins_100g": 3}
    if status is not None:
        nutrition["_reference_verification"] = {"status": status, "source": "fixture"}
    with SessionLocal() as db:
        item = Item(name="Legacy", barcode="123456789012", nutrition=nutrition,
                    nutrition_estimated=estimated)
        db.add(item)
        db.commit()
        item_id = str(item.id)
    response = client.post("/api/barcodes/scan", json={"barcode": "123456789012", "auto_create": False})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["nutrition_reference"]["status"] == (status or "legacy_unknown")
    result = client.get(f"/api/items/{item_id}").json()
    assert result["energy_kcal_100g"] == (90 if allowed else None)
    assert result["protein_g_100g"] == (3 if allowed else None)
    assert result["nutrition"] == nutrition  # no historical rewrite


@pytest.mark.parametrize("authority", ["human_selected", "verified", "label_exact"])
@pytest.mark.parametrize("edit", ["ui_roundtrip", "name_only", "partial_unchanged", "protein_change"])
def test_reference_precision_survives_unchanged_and_unrelated_edits(client, monkeypatch, authority, edit):
    macros = {"energy-kcal_100g": 23.456, "proteins_100g": 2.123, "sodium_100g": 0.003}
    food = NutritionEstimate(macros, "Fine precision reference", 123)
    row = client.post("/api/items", json={"name": "Fine precision food", "barcode": "123456789012"}).json()
    if authority == "human_selected":
        monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: food)
        original = client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123}).json()
    elif authority == "verified":
        monkeypatch.setattr(barcodes, "lookup_external_merged", lambda *a, **k: None)
        monkeypatch.setattr(barcodes, "estimate_nutrition_by_name", lambda *a, **k: food)
        monkeypatch.setattr(food_judge, "same_food", lambda *a, **k: food_judge.FoodVerdict(True, "fixture"))
        original = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    else:
        monkeypatch.setattr(barcodes, "lookup_external_merged", lambda *a, **k: ProductResult(
            "123456789012", "Fine precision food", nutrition=macros, source="fixture"))
        original = client.post(f"/api/items/{row['id']}/rescan").json()["item"]
    assert original["sodium_g_100g"] == 0
    assert original["nutrition"]["sodium_100g"] == 0.003
    if edit == "ui_roundtrip":
        # The actual editor submits all visible macro fields, with rounded columns.
        payload = {field: original[field] for field in (
            "energy_kcal_100g", "protein_g_100g", "fat_g_100g", "carbs_g_100g")}
        payload["name"] = "Renamed food"
    elif edit == "name_only":
        payload = {"name": "Renamed food"}
    elif edit == "partial_unchanged":
        payload = {"protein_g_100g": original["protein_g_100g"]}
    else:
        payload = {"protein_g_100g": 4}
    response = client.put(f"/api/items/{row['id']}", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["nutrition"]["energy-kcal_100g"] == 23.456
    assert result["nutrition"]["sodium_100g"] == 0.003
    if edit == "protein_change":
        assert result["nutrition_reference"]["status"] == "manual"
        assert result["nutrition_estimated"] is False
        assert result["protein_g_100g"] == result["nutrition"]["proteins_100g"] == 4
    else:
        assert result["nutrition"] == original["nutrition"]
        assert result["nutrition_reference"] == original["nutrition_reference"]
        assert result["nutrition_estimated"] == original["nutrition_estimated"]


def test_explicit_json_fine_precision_edit_is_manual_even_when_column_is_unchanged(client, monkeypatch):
    monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": 23.456, "sodium_100g": 0.003}, "Chosen", 123))
    row = client.post("/api/items", json={"name": "Food"}).json()
    original = client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123}).json()
    raw = dict(original["nutrition"])
    raw["sodium_100g"] = 0.004
    result = client.put(f"/api/items/{row['id']}", json={"nutrition": raw}).json()
    assert result["sodium_g_100g"] == original["sodium_g_100g"] == 0
    assert result["nutrition"]["sodium_100g"] == 0.004
    assert result["nutrition"]["energy-kcal_100g"] == 23.456
    assert result["nutrition_reference"]["status"] == "manual"


def test_unchanged_null_columns_do_not_erase_legacy_reference_json(client):
    raw = {"energy-kcal_100g": 23.456, "sodium_100g": 0.003}
    with SessionLocal() as db:
        item = Item(name="Legacy estimate", nutrition=raw, nutrition_estimated=True)
        db.add(item)
        db.commit()
        item_id = str(item.id)
    result = client.put(f"/api/items/{item_id}", json={"name": "Renamed", "energy_kcal_100g": None}).json()
    assert result["nutrition"] == raw
    assert result["energy_kcal_100g"] is None
    assert result["nutrition_estimated"] is True


@pytest.mark.parametrize("energy,protein", [(23.455, 2.125), (23.465, 2.135), (23.445, 2.145)])
def test_midpoint_reference_json_survives_actual_numeric_roundtrip(client, monkeypatch, energy, protein):
    monkeypatch.setattr(barcodes, "_resolve_usda_food", lambda *a, **k: NutritionEstimate(
        {"energy-kcal_100g": energy, "proteins_100g": protein}, "Chosen midpoint", 123))
    row = client.post("/api/items", json={"name": "Food"}).json()
    original = client.post(f"/api/items/{row['id']}/nutrition-from-usda", json={"fdc_id": 123}).json()
    for payload in ({"energy_kcal_100g": original["energy_kcal_100g"],
                     "protein_g_100g": original["protein_g_100g"]}, {"nutrition": original["nutrition"]}):
        response = client.put(f"/api/items/{row['id']}", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["nutrition"] == original["nutrition"]
        assert response.json()["nutrition_reference"] == original["nutrition_reference"]


def test_structured_rounding_follows_postgres_numeric_half_up_and_freezes_unchanged_echo():
    # PostgreSQL rounds positive Numeric ties away from zero; SQLite's float
    # result processor differs, so assert the production contract directly too.
    from modules.catalog.services.nutrition_authority import explicit_nutrition
    original = {"proteins_100g": 2.134, "_reference_verification": {"status": "verified"}}
    item = Item(name="Precision contract", nutrition=original, protein_g_100g=Decimal("2.13"))
    unchanged = {"protein_g_100g": Decimal("2.134")}
    explicit_nutrition(unchanged, set(unchanged), item)
    assert unchanged["protein_g_100g"] == Decimal("2.13")
    assert unchanged["nutrition"] == original
    item.protein_g_100g = Decimal("2.12")
    midpoint = {"protein_g_100g": Decimal("2.125")}
    explicit_nutrition(midpoint, set(midpoint), item)
    assert midpoint["nutrition"]["_reference_verification"]["status"] == "manual"
    assert midpoint["nutrition"]["proteins_100g"] == 2.125
