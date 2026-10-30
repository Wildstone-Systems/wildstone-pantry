"""ADR 0015 Phase 1 — recipe availability matches stock by canonical ingredient
identity, not exact item_id. The bug this fixes: a recipe's ingredient and the
product you stocked are *different catalog records*, so every recipe showed all
ingredients missing even when stocked. Equality of the canonical key unifies them;
cook then deducts the concrete stocked item."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def _personal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)


def _item(client: httpx.Client, name: str) -> str:
    resp = client.post("/api/items", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _location(client: httpx.Client, name: str) -> str:
    return client.post("/api/locations", json={"name": name}).json()["id"]


def _add_stock(client: httpx.Client, *, item_id: str, location_id: str, quantity: float) -> None:
    resp = client.post(
        "/api/inventory/add",
        json={"item_id": item_id, "location_id": location_id, "quantity": quantity, "unit": "each"},
    )
    assert resp.status_code in {200, 201}, resp.text


def _recipe(client: httpx.Client, name: str, ingredients: list[dict]) -> str:
    recipe_id = client.post("/api/recipes", json={"name": name}).json()["id"]
    client.put(f"/api/recipes/{recipe_id}/ingredients", json=ingredients)
    return recipe_id


def _suggestion(client: httpx.Client, recipe_id: str) -> dict:
    rows = client.get("/api/recipes/suggestions").json()["suggestions"]
    return next(r for r in rows if r["recipe_id"] == recipe_id)


def test_brand_and_size_divergent_records_match_and_cook(client: httpx.Client) -> None:
    # The recipe calls for a generic "Garlic Powder"; the pantry holds a *different*
    # catalog record, "McCormick Garlic Powder, 3 oz". Both canonicalize to
    # "garlic powder", so they are the same ingredient.
    recipe_item = _item(client, "Garlic Powder")
    stocked = _item(client, "McCormick Garlic Powder, 3 oz")
    assert recipe_item != stocked  # genuinely separate records

    kitchen = _location(client, "Kitchen")
    _add_stock(client, item_id=stocked, location_id=kitchen, quantity=5)

    recipe_id = _recipe(client, "Rub", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])

    # Availability counts the stocked product even though it's a different record.
    s = _suggestion(client, recipe_id)
    assert s["available_score"] == pytest.approx(1.0)
    assert s["missing_items"] == []

    # Cooking deducts the concrete stocked item, not the empty recipe record.
    cooked = client.post(f"/api/recipes/{recipe_id}/cook", json={"servings": 1})
    assert cooked.status_code == 200, cooked.text

    def qty(item_id: str) -> float:
        rows = client.get(f"/api/inventory?location_id={kitchen}").json()["items"]
        return float(next((r["quantity"] for r in rows if r["item_id"] == item_id), 0))

    assert qty(stocked) == pytest.approx(4.0)  # deducted from McCormick jar
    assert qty(recipe_item) == pytest.approx(0.0)  # the generic record was never stocked


def test_identical_names_as_separate_records_now_match(client: httpx.Client) -> None:
    # The classic collision: recipe's "Paprika" and a scanned "Paprika" are two
    # rows with different ids. Exact-id matching missed this; canonical equality
    # catches it.
    recipe_item = _item(client, "Paprika")
    scanned = _item(client, "Paprika")
    kitchen = _location(client, "Pantry")
    _add_stock(client, item_id=scanned, location_id=kitchen, quantity=2)

    recipe_id = _recipe(client, "Goulash", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(1.0)


def test_flavor_divergent_records_do_NOT_match(client: httpx.Client) -> None:
    # No false positives: plain "Milk" is not satisfied by "Strawberry Milk"
    # ("milk" != "milk strawberry"). This ingredient stays honestly missing.
    recipe_item = _item(client, "Milk")
    other = _item(client, "Strawberry Milk")
    kitchen = _location(client, "Fridge")
    _add_stock(client, item_id=other, location_id=kitchen, quantity=3)

    recipe_id = _recipe(client, "Cereal", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])
    s = _suggestion(client, recipe_id)
    assert s["available_score"] == pytest.approx(0.0)
    assert "Milk" in s["missing_items"]


# --- ADR 0015 Phase 2: tight-subset suggestions + confirm-to-learn -----------


def _availability(client: httpx.Client, recipe_id: str) -> list[dict]:
    resp = client.get(f"/api/recipes/{recipe_id}/availability")
    assert resp.status_code == 200, resp.text
    return resp.json()["ingredients"]


def test_subset_suggestion_confirm_flips_missing_to_have(client: httpx.Client) -> None:
    # Recipe wants generic "Cheddar Cheese"; pantry holds "Shredded Cheddar
    # Cheese" — a tight-subset (one extra token). Phase 1 leaves it missing; Phase
    # 2 offers it as a suggestion, and confirming makes it count everywhere.
    recipe_item = _item(client, "Cheddar Cheese")
    shredded = _item(client, "Shredded Cheddar Cheese")
    kitchen = _location(client, "Fridge")
    _add_stock(client, item_id=shredded, location_id=kitchen, quantity=4)

    recipe_id = _recipe(client, "Mac", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])

    # Not equality-matched yet → suggested, with the shredded product offered.
    ing = _availability(client, recipe_id)[0]
    assert ing["status"] == "suggested"
    assert [s["item_id"] for s in ing["suggestions"]] == [shredded]
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(0.0)

    # Confirm: the shredded product IS this ingredient.
    confirm = client.post(
        f"/api/recipes/ingredients/{ing['ingredient_id']}/confirm-item",
        json={"item_id": shredded},
    )
    assert confirm.status_code == 200, confirm.text

    # Now it's identity — have, no more suggestion, and the recipe scores it.
    ing2 = _availability(client, recipe_id)[0]
    assert ing2["status"] == "have"
    assert ing2["suggestions"] == []
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(1.0)

    # And the learned alias applies to a *different* recipe using the same key.
    other = _recipe(client, "Quesadilla", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])
    assert _suggestion(client, other)["available_score"] == pytest.approx(1.0)


def test_availability_reports_have_and_missing(client: httpx.Client) -> None:
    have_item = _item(client, "Garlic Powder")
    stocked = _item(client, "McCormick Garlic Powder, 3 oz")  # same key → have
    missing_item = _item(client, "Saffron")  # nothing on hand, no subset either
    kitchen = _location(client, "Pantry")
    _add_stock(client, item_id=stocked, location_id=kitchen, quantity=2)

    recipe_id = _recipe(
        client,
        "Rice",
        [
            {"item_id": have_item, "quantity_required": 1, "unit": "each"},
            {"item_id": missing_item, "quantity_required": 1, "unit": "each"},
        ],
    )
    by_name = {i["item_name"]: i for i in _availability(client, recipe_id)}
    assert by_name["Garlic Powder"]["status"] == "have"
    assert by_name["Saffron"]["status"] == "missing"


# --- ADR 0015 Phase 3: canonical directional substitutions -------------------


def test_approved_canonical_substitute_counts_as_available(client: httpx.Client) -> None:
    # A learned/approved sub (Sour Cream → Plain Greek Yogurt) whose target is on
    # hand makes the ingredient available "via substitute", and scores the recipe.
    sour_cream = _item(client, "Sour Cream")
    greek = _item(client, "Plain Greek Yogurt")
    kitchen = _location(client, "Fridge")
    _add_stock(client, item_id=greek, location_id=kitchen, quantity=3)

    made = client.post(
        "/api/substitutions",
        json={"from_item_id": sour_cream, "to_item_id": greek, "ratio": 1, "outcome": "direct"},
    )
    assert made.status_code == 201, made.text

    recipe_id = _recipe(client, "Dip", [{"item_id": sour_cream, "quantity_required": 1, "unit": "each"}])

    ing = _availability(client, recipe_id)[0]
    assert ing["status"] == "substitute"
    assert ing["available_via"]["to_label"] == "Plain Greek Yogurt"
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(1.0)


def test_seed_substitution_suggestion_then_approve(client: httpx.Client) -> None:
    from app.db.session import SessionLocal
    from app.models import IngredientSubstitution
    from modules.catalog.services.canonical import canonical_ingredient

    # An UNAPPROVED (seed-style) sub, inserted like the migration seed does.
    recipe_item = _item(client, "Sour Cream")
    greek = _item(client, "Plain Greek Yogurt")
    kitchen = _location(client, "Fridge")
    _add_stock(client, item_id=greek, location_id=kitchen, quantity=3)

    db = SessionLocal()
    sub = IngredientSubstitution(
        from_canonical=canonical_ingredient("Sour Cream"),
        to_canonical=canonical_ingredient("Plain Greek Yogurt"),
        from_label="Sour Cream",
        to_label="Plain Greek Yogurt",
        approved=False,
        outcome="direct",
    )
    db.add(sub)
    db.commit()
    sub_id = str(sub.id)
    db.close()

    recipe_id = _recipe(client, "Dip", [{"item_id": recipe_item, "quantity_required": 1, "unit": "each"}])

    # Unapproved → offered as a substitution suggestion, not yet counted.
    ing = _availability(client, recipe_id)[0]
    assert ing["status"] == "suggested"
    assert [s["substitution_id"] for s in ing["substitution_suggestions"]] == [sub_id]
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(0.0)

    # Approve it → the ingredient flips to available via the substitute.
    approve = client.post(f"/api/recipes/substitutions/{sub_id}/approve")
    assert approve.status_code == 200, approve.text

    ing2 = _availability(client, recipe_id)[0]
    assert ing2["status"] == "substitute"
    assert _suggestion(client, recipe_id)["available_score"] == pytest.approx(1.0)
