"""Two defects that made imported recipes unusable, reported together:

  * an agent sending {"name": "2 avocados"} created a catalog item literally
    called "2 avocados" AND recorded the amount as 1 — so the recipe asked for
    half what it needed and could never match the avocado already on the shelf
  * plural and singular produced different canonical keys, so "avocados" never
    matched "avocado" even once the name was clean
"""

from __future__ import annotations

import httpx
import pytest

from modules.catalog.services.canonical import canonical_ingredient


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.c", "password": "TestPass123!"})
    t = client.post("/api/auth/login", json={"email": "a@b.c", "password": "TestPass123!"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.parametrize(
    "plural,singular",
    [
        ("avocados", "avocado"),
        ("eggs", "egg"),
        ("tomatoes", "tomato"),
        ("onions", "onion"),
        ("berries", "berry"),
        ("peaches", "peach"),
        ("boxes", "box"),
    ],
)
def test_plurals_share_a_key_with_their_singular(plural: str, singular: str) -> None:
    assert canonical_ingredient(plural) == canonical_ingredient(singular)


@pytest.mark.parametrize("word", ["hummus", "molasses", "asparagus", "couscous", "grass", "oats"])
def test_words_that_merely_end_in_s_are_left_alone(word: str) -> None:
    """A wrong fold silently merges two real ingredients — worse than a miss."""
    assert canonical_ingredient(word) == canonical_ingredient(word)
    assert word in (canonical_ingredient(word) or "")


def test_a_leading_count_is_lifted_out_of_the_name(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Guacamole",
            "servings": 4,
            "ingredients": [{"name": "2 avocados", "unit": "each"}],
            "create_missing_items": True,
        },
        headers=h,
    ).json()

    ing = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"][0]
    assert float(ing["quantity_required"]) == 2  # not the 1.0 default
    assert ing["item_name"] == "avocados"  # the digit is gone from the catalog name


def test_a_lifted_count_still_matches_an_existing_singular_item(client: httpx.Client) -> None:
    """The two fixes together: "2 avocados" resolves to the avocado you have."""
    h = _admin(client)
    existing = client.post("/api/items", json={"name": "avocado"}, headers=h).json()
    before = len(client.get("/api/items", headers=h).json()["items"])

    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Tacos",
            "servings": 2,
            "ingredients": [{"name": "2 avocados", "unit": "each"}],
            "create_missing_items": True,
        },
        headers=h,
    ).json()

    assert len(client.get("/api/items", headers=h).json()["items"]) == before  # nothing new
    ing = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"][0]
    assert ing["item_id"] == existing["id"]
    assert float(ing["quantity_required"]) == 2


def test_an_explicit_quantity_always_wins(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Explicit",
            "servings": 1,
            "ingredients": [{"name": "3 lemons", "quantity": 5, "unit": "each"}],
            "create_missing_items": True,
        },
        headers=h,
    ).json()
    ing = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"][0]
    assert float(ing["quantity_required"]) == 5  # the caller meant 5, not 3


@pytest.mark.parametrize("name", ["2% milk", "7-Up", "100% orange juice"])
def test_real_product_names_starting_with_digits_are_untouched(client: httpx.Client, name: str) -> None:
    """Requiring whitespace after the number is what protects these."""
    h = _admin(client)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": f"Uses {name}",
            "servings": 1,
            "ingredients": [{"name": name, "unit": "each"}],
            "create_missing_items": True,
        },
        headers=h,
    ).json()
    ing = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"][0]
    assert ing["item_name"] == name
    assert float(ing["quantity_required"]) == 1


def test_fractions_in_a_name_are_understood(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Half",
            "servings": 1,
            "ingredients": [{"name": "1/2 onion", "unit": "each"}],
            "create_missing_items": True,
        },
        headers=h,
    ).json()
    ing = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"][0]
    assert float(ing["quantity_required"]) == 0.5
    assert ing["item_name"] == "onion"
