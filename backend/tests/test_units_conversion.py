"""Unit conversion + ingredient-reference hint resolution (step 5 / ADR 0011 D2)."""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.db.session import SessionLocal
from app.models import Item
from modules.catalog.services import units
from modules.catalog.services.ingredient_reference import (
    macros_for_amount,
    resolve_hints,
    seed_reference,
)


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    return {"Authorization": f"Bearer {login.json()['token']}"}


# --- pure conversion -------------------------------------------------------


def test_mass_units_exact() -> None:
    assert units.to_grams(Decimal("2"), "kg") == Decimal("2000")
    assert units.to_grams(Decimal("1"), "lb") == Decimal("453.59237")
    assert units.to_grams(Decimal("16"), "oz") == Decimal("453.592370")


def test_volume_needs_density() -> None:
    assert units.to_grams(Decimal("2"), "cup") is None
    grams = units.to_grams(Decimal("2"), "cup", density_g_per_ml=Decimal("1"))
    assert grams is not None and abs(grams - Decimal("473.176")) < Decimal("0.01")


def test_each_needs_grams_per_each() -> None:
    assert units.to_grams(Decimal("3"), "each") is None
    assert units.to_grams(Decimal("3"), "each", grams_per_each=Decimal("50")) == Decimal("150")


def test_unknown_unit_returns_none() -> None:
    assert units.to_grams(Decimal("1"), "smidgen") is None


def test_aliases_normalize() -> None:
    assert units.normalize_unit("Grams") == "g"
    assert units.to_grams(Decimal("1"), "tablespoons", density_g_per_ml=Decimal("1")) == Decimal("14.7868")


# --- hint resolution + macros ---------------------------------------------


def test_resolve_hints_item_override_wins(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        seed_reference(db)  # butter density seeded ~0.96
        item = Item(name="Butter", density_g_per_ml=Decimal("0.80"))
        db.add(item)
        db.commit()
        db.refresh(item)
        density, grams_each = resolve_hints(db, item)
        assert density == Decimal("0.80")  # override, not the 0.96 seed
    finally:
        db.close()


def test_resolve_hints_falls_back_to_seed(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        seed_reference(db)
        item = Item(name="Honey")  # no override → seed density 1.42
        db.add(item)
        db.commit()
        db.refresh(item)
        density, _ = resolve_hints(db, item)
        assert density == Decimal("1.4200")
    finally:
        db.close()


def test_macros_for_amount_scales_and_partials(client: httpx.Client) -> None:
    db = SessionLocal()
    try:
        seed_reference(db)
        # 100g basis: 364 kcal, 10g protein. 200g flour → 2x.
        flour = Item(
            name="All Purpose Flour",
            energy_kcal_100g=Decimal("364"),
            protein_g_100g=Decimal("10"),
            fat_g_100g=Decimal("1"),
            carbs_g_100g=Decimal("76"),
        )
        db.add(flour)
        db.commit()
        db.refresh(flour)

        macros = macros_for_amount(db, flour, Decimal("200"), "g")
        assert macros is not None
        assert macros["calories"] == Decimal("728.00")
        assert macros["protein_g"] == Decimal("20.00")

        # Volume works via the seeded flour density.
        vol = macros_for_amount(db, flour, Decimal("1"), "cup")
        assert vol is not None and vol["calories"] > 0

        # An item with no density/grams-per-each and a volume amount → None (partial).
        mystery = Item(name="Mystery Powder", energy_kcal_100g=Decimal("100"))
        db.add(mystery)
        db.commit()
        db.refresh(mystery)
        assert macros_for_amount(db, mystery, Decimal("1"), "cup") is None
    finally:
        db.close()


# --- catalog schema + reference API ---------------------------------------


def test_item_exposes_macro_fields(client: httpx.Client) -> None:
    h = _admin_headers(client)
    resp = client.post(
        "/api/items",
        json={
            "name": "Chicken Breast",
            "energy_kcal_100g": 165,
            "protein_g_100g": 31,
            "fat_g_100g": 3.6,
            "carbs_g_100g": 0,
            "grams_per_each": 174,
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(str(body["protein_g_100g"])) == Decimal("31")
    assert Decimal(str(body["grams_per_each"])) == Decimal("174")


def test_reference_crud_roundtrip(client: httpx.Client) -> None:
    h = _admin_headers(client)
    created = client.post(
        "/api/ingredient-reference",
        json={"name": "Coconut Oil", "category": "oil", "density_g_per_ml": 0.92},
        headers=h,
    )
    assert created.status_code == 201, created.text
    ref_id = created.json()["id"]
    assert created.json()["name"] == "coconut oil"  # normalized

    listing = client.get("/api/ingredient-reference", headers=h)
    assert any(r["id"] == ref_id for r in listing.json()["references"])

    updated = client.put(
        f"/api/ingredient-reference/{ref_id}", json={"density_g_per_ml": 0.91}, headers=h
    )
    assert Decimal(str(updated.json()["density_g_per_ml"])) == Decimal("0.91")

    deleted = client.delete(f"/api/ingredient-reference/{ref_id}", headers=h)
    assert deleted.status_code == 204


def test_reference_user_row_used_by_resolve_hints(client: httpx.Client) -> None:
    h = _admin_headers(client)
    client.post(
        "/api/ingredient-reference",
        json={"name": "Fictional Grain", "density_g_per_ml": 0.7},
        headers=h,
    )
    db = SessionLocal()
    try:
        item = Item(name="Fictional Grain")
        db.add(item)
        db.commit()
        db.refresh(item)
        density, _ = resolve_hints(db, item)
        assert density == Decimal("0.7000")
    finally:
        db.close()
