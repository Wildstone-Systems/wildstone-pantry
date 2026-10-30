"""Newly created recipe ingredients get looked up, safely and boundedly."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.db import SessionLocal
from app.models import Item
from modules.catalog.services.enrichment import MAX_INLINE_ENRICHMENTS, enrich_item, enrich_new_items


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _item(db, name: str) -> Item:
    row = Item(name=f"{name} {uuid.uuid4().hex[:6]}")
    db.add(row)
    db.commit()
    return row


def test_an_unverified_density_is_never_written_unattended(db_session, monkeypatch):
    """The gate's verdict is the whole safety story. With no human in the loop,
    only a confirmed match may be committed; an unconfirmed one is left for the
    review queue where someone can look at it."""
    from modules.catalog.services import enrichment

    class Unverified:
        density_g_per_ml = Decimal("0.5")
        source_description = "Something, vaguely similar"
        verified = False

    monkeypatch.setattr(
        "modules.catalog.services.conversions.suggest_density", lambda *a, **k: Unverified()
    )
    monkeypatch.setattr(enrichment, "MAX_INLINE_ENRICHMENTS", 8)

    item = _item(db_session, "Mystery Spice")
    result = enrich_item(db_session, item, with_density=True)
    db_session.refresh(item)

    assert item.density_g_per_ml is None
    assert "density" not in result


def test_a_verified_density_is_written(db_session, monkeypatch):
    class Verified:
        density_g_per_ml = Decimal("0.4666")
        source_description = "Spices, pepper, black"
        verified = True

    monkeypatch.setattr(
        "modules.catalog.services.conversions.suggest_density", lambda *a, **k: Verified()
    )
    item = _item(db_session, "Black Pepper")
    result = enrich_item(db_session, item, with_density=True)
    db_session.refresh(item)

    assert item.density_g_per_ml == Decimal("0.4666")
    assert result["density"] == "Spices, pepper, black"


def test_a_provider_blowing_up_never_fails_the_import(db_session, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("USDA is down")

    monkeypatch.setattr("modules.catalog.services.conversions.suggest_density", explode)
    monkeypatch.setattr("modules.catalog.services.barcodes._apply_estimated_nutrition", explode)

    item = _item(db_session, "Unlucky")
    assert enrich_item(db_session, item) == {}  # no crash, nothing filled


def test_a_bulk_import_is_capped_and_says_so(db_session, monkeypatch):
    """A bulk import can create dozens of items. Enriching all of them inline
    would turn one request into minutes and rate-limit the provider for
    everything after it."""
    calls: list[str] = []
    monkeypatch.setattr(
        "modules.catalog.services.enrichment.enrich_item",
        lambda db, item, **k: calls.append(item.name) or {},
    )

    items = [_item(db_session, f"Bulk{i}") for i in range(MAX_INLINE_ENRICHMENTS + 5)]
    report = enrich_new_items(db_session, items)

    assert len(calls) == MAX_INLINE_ENRICHMENTS
    # Reported, not silently dropped — the rest land in the review queue.
    assert report["skipped"] == 5


def test_importing_a_recipe_does_not_fail_when_enrichment_does(client, monkeypatch):
    """End-to-end: the import path must survive a dead provider."""
    monkeypatch.setattr(
        "modules.catalog.services.enrichment.enrich_new_items",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    tag = uuid.uuid4().hex[:6]
    res = client.post(
        "/api/recipes",
        json={
            "name": f"Resilient {tag}",
            "servings": 2,
            "ingredients": [{"name": f"Brand New Thing {tag}", "quantity": 1, "unit": "tsp"}],
            "create_missing_items": True,
        },
    )
    assert res.status_code in (200, 201), res.text


@pytest.mark.parametrize("checked,same,expected", [(True, False, False), (False, False, False), (True, True, True)])
def test_nutrition_verification_precedes_persistence(db_session, monkeypatch, checked, same, expected):
    from modules.automation import food_judge
    from modules.catalog.services.barcode_providers import NutritionEstimate

    monkeypatch.setattr("modules.catalog.services.barcodes.estimate_nutrition_by_name",
                        lambda *a, **k: NutritionEstimate({"energy-kcal_100g": 23}, "Tomatillos, raw", 168566))
    item = _item(db_session, "tomatillos")

    def judge(*a, **k):
        # A fresh connection observes no committed estimate, even during verification.
        with SessionLocal() as observer:
            stored = observer.get(Item, item.id)
            assert stored.nutrition is None
            assert stored.energy_kcal_100g is None
        assert item.nutrition is None  # also no pending ORM mutation
        return food_judge.FoodVerdict(same, "fixture verdict", checked)

    monkeypatch.setattr(food_judge, "same_food", judge)
    result = enrich_item(db_session, item, with_density=False)
    db_session.refresh(item)
    assert ("nutrition" in result) is expected
    assert (item.energy_kcal_100g is not None) is expected
    assert (item.nutrition is not None) is expected
