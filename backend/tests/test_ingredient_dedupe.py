"""Recipe imports and agent writes must reuse existing catalog items.

Reported: every agent recipe import created a fresh batch of generic items
("Parmesan cheese", "ground chicken", ...) even when the pantry already had
them, filling the catalog with near-duplicates that fragment stock and
nutrition. Name resolution now falls back to the ADR 0015 canonical identity
key instead of demanding an exact string match.
"""

from __future__ import annotations

import httpx


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.c", "password": "TestPass123!"})
    t = client.post("/api/auth/login", json={"email": "a@b.c", "password": "TestPass123!"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def _items(client, h) -> list[dict]:
    return client.get("/api/items", headers=h).json()["items"]


def _recipe(client, h, name: str, ingredient_names: list[str]) -> dict:
    return client.post(
        "/api/recipes",
        json={
            "name": name,
            "servings": 4,
            "ingredients": [{"name": n, "quantity": 1, "unit": "each"} for n in ingredient_names],
            "create_missing_items": True,
        },
        headers=h,
    ).json()


def test_case_and_wording_variants_reuse_one_item(client: httpx.Client) -> None:
    h = _admin(client)
    _recipe(client, h, "First", ["Parmesan cheese"])
    before = len(_items(client, h))

    # The same ingredient written three plausible ways across later imports.
    _recipe(client, h, "Second", ["parmesan cheese"])
    _recipe(client, h, "Third", ["PARMESAN CHEESE"])
    _recipe(client, h, "Fourth", ["cheese, parmesan"])

    assert len(_items(client, h)) == before, [i["name"] for i in _items(client, h)]


def test_a_branded_pantry_product_absorbs_the_generic_recipe_line(client: httpx.Client) -> None:
    """The pantry already has the real product; a recipe naming the bare
    ingredient must point at it rather than mint a generic twin."""
    h = _admin(client)
    branded = client.post(
        "/api/items", json={"name": "Kraft Grated Parmesan Cheese", "brand": "Kraft"}, headers=h
    ).json()
    before = len(_items(client, h))

    recipe = _recipe(client, h, "Pasta", ["grated parmesan cheese"])
    assert len(_items(client, h)) == before  # nothing new created

    ingredients = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"]
    assert ingredients[0]["item_id"] == branded["id"]


def test_the_generic_is_preferred_when_several_match(client: httpx.Client) -> None:
    h = _admin(client)
    generic = client.post("/api/items", json={"name": "olive oil"}, headers=h).json()
    client.post("/api/items", json={"name": "olive oil extra virgin cold pressed"}, headers=h)

    recipe = _recipe(client, h, "Dressing", ["Olive Oil"])
    ingredients = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"]
    assert ingredients[0]["item_id"] == generic["id"]


def test_genuinely_different_ingredients_are_not_merged(client: httpx.Client) -> None:
    """The guard against over-merging: these share a word but are not the same
    thing, and collapsing them would be far worse than a duplicate."""
    h = _admin(client)
    before = len(_items(client, h))
    _recipe(client, h, "Mixed", ["parmesan cheese", "cream cheese", "cheddar cheese"])
    assert len(_items(client, h)) == before + 3

    before = len(_items(client, h))
    _recipe(client, h, "Oils", ["olive oil", "sesame oil"])
    assert len(_items(client, h)) == before + 2


def test_reimporting_the_same_recipe_creates_no_new_items(client: httpx.Client) -> None:
    """The exact reported flow: an agent re-imports and the catalog grows again."""
    lines = ["ground chicken", "baby arugula", "Italian parsley", "mozzarella cheese", "panko bread crumbs"]
    h = _admin(client)
    _recipe(client, h, "Chicken Parm Meatballs", lines)
    after_first = len(_items(client, h))

    _recipe(client, h, "Chicken Parm Meatballs v2", lines)
    assert len(_items(client, h)) == after_first


def test_a_scanned_product_gets_an_identity_key_so_recipes_can_match_it(
    client: httpx.Client, monkeypatch
) -> None:
    """Barcode-created items used to be keyless, so a recipe naming the bare
    ingredient could never find the jar already on the shelf."""
    from modules.catalog.services import barcodes as barcodes_service
    from modules.catalog.services.barcode_providers import ProductResult

    monkeypatch.setattr(
        barcodes_service,
        "lookup_external",
        lambda code, **k: ProductResult(
            barcode=code, name="Kraft Grated Parmesan Cheese", brand="Kraft", source="off"
        ),
    )
    h = _admin(client)
    scanned = client.post(
        "/api/barcodes/scan", json={"barcode": "021000613014", "auto_create": True}, headers=h
    ).json()
    assert scanned["item_id"]

    before = len(_items(client, h))
    recipe = _recipe(client, h, "Pasta Bake", ["grated parmesan cheese"])
    assert len(_items(client, h)) == before  # matched the scanned jar, made nothing

    ingredients = client.get(f"/api/recipes/{recipe['id']}", headers=h).json()["ingredients"]
    assert str(ingredients[0]["item_id"]) == str(scanned["item_id"])
