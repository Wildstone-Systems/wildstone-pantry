"""Rescan merges data across providers (ADR 0013 follow-up).

First-hit-wins is fine for a fast scan, but a rescan should be complete: OFF
often has nutrition but no package size, while USDA carries the size in its
name. The merged lookup gives the item both.
"""

from __future__ import annotations

from modules.catalog.services import barcode_providers as p


def _pr(**kw):
    base = {"barcode": "x", "name": "prod"}
    base.update(kw)
    return p.ProductResult(**base)


def test_merged_lookup_borrows_size_from_usda_keeps_off_nutrition(monkeypatch) -> None:
    off = _pr(
        name="2% Reduced Fat Milk",
        source="open_food_facts",
        brand="Prairie Farms",
        nutrition={"energy-kcal_100g": 50},
    )  # nutrition + brand, NO size
    usda = _pr(
        name="Prairie Farms 2% Milk 1 Gallon",
        source="usda_fdc",
        size_quantity=1.0,
        size_unit="gal",
        size_description="1 Gallon",
    )  # size, no nutrition

    monkeypatch.setattr(p, "lookup_open_food_facts", lambda bc: off)
    monkeypatch.setattr(p, "lookup_usda_fdc", lambda bc, api_key=None: usda)

    merged = p.lookup_external_merged("x", usda_api_key="k")
    assert merged is not None
    assert merged.source == "open_food_facts"  # OFF is still the primary (first hit)
    assert merged.nutrition == {"energy-kcal_100g": 50}  # kept OFF's nutrition
    assert (merged.size_quantity, merged.size_unit) == (1.0, "gal")  # borrowed USDA's size
    assert merged.size_description == "1 Gallon"


def test_merged_lookup_returns_none_when_all_miss(monkeypatch) -> None:
    monkeypatch.setattr(p, "lookup_open_food_facts", lambda bc: None)
    monkeypatch.setattr(p, "lookup_usda_fdc", lambda bc, api_key=None: None)
    assert p.lookup_external_merged("x", usda_api_key="k") is None
