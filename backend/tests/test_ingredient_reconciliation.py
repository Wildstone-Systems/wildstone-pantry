"""Household-wide ingredient reconciliation (ADR 0015 §5).

Per-recipe availability already flags "this might be the thing on your shelf",
but one recipe at a time — so the same question reappears for every recipe using
that ingredient. This answers each once, and confirming resolves them all.
"""

from __future__ import annotations

import httpx


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "a@b.c", "password": "TestPass123!"})
    t = client.post("/api/auth/login", json={"email": "a@b.c", "password": "TestPass123!"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def _stock(client, h, name: str, qty: float = 5):
    item = client.post("/api/items", json={"name": name}, headers=h).json()
    loc = client.get("/api/locations", headers=h).json()["locations"]
    loc_id = loc[0]["id"] if loc else client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    client.post(
        "/api/inventory/add",
        json={"item_id": item["id"], "location_id": loc_id, "quantity": qty, "unit": "each"},
        headers=h,
    )
    return item


def _recipe(client, h, name: str, ingredients: list[str]):
    return client.post(
        "/api/recipes",
        json={
            "name": name,
            "servings": 2,
            "ingredients": [{"name": n, "quantity": 1, "unit": "each"} for n in ingredients],
            "create_missing_items": True,
        },
        headers=h,
    ).json()


def test_offers_a_stocked_product_as_a_match_for_a_generic_ingredient(client: httpx.Client) -> None:
    h = _admin(client)
    shelf = _stock(client, h, "Organic Virgin Coconut Oil")
    _recipe(client, h, "Curry", ["coconut oil"])

    matches = client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"]
    assert len(matches) == 1
    assert matches[0]["item_name"] == "coconut oil"
    assert matches[0]["recipe_names"] == ["Curry"]
    assert [c["item_id"] for c in matches[0]["candidates"]] == [shelf["id"]]


def test_one_confirmation_resolves_every_recipe_using_that_ingredient(client: httpx.Client) -> None:
    """The reason this is a household-wide queue rather than a per-recipe prompt."""
    h = _admin(client)
    shelf = _stock(client, h, "Organic Virgin Coconut Oil")
    r1 = _recipe(client, h, "Curry", ["coconut oil"])
    r2 = _recipe(client, h, "Granola", ["coconut oil"])

    match = client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"][0]
    assert sorted(match["recipe_names"]) == ["Curry", "Granola"]

    resp = client.post(
        f"/api/recipes/ingredients/{match['ingredient_id']}/confirm-item",
        json={"item_id": shelf["id"]},
        headers=h,
    )
    assert resp.status_code == 200, resp.text

    # Both recipes now see it as on hand...
    for r in (r1, r2):
        rows = client.get(f"/api/recipes/{r['id']}/availability", headers=h).json()["ingredients"]
        assert rows[0]["status"] == "have", rows[0]
    # ...and the queue is empty.
    assert client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"] == []


def test_ingredients_already_satisfied_are_not_offered(client: httpx.Client) -> None:
    h = _admin(client)
    _stock(client, h, "coconut oil")
    _recipe(client, h, "Curry", ["coconut oil"])
    assert client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"] == []


def test_products_you_do_not_have_are_never_offered(client: httpx.Client) -> None:
    """Linking to something out of stock wouldn't change any recipe's availability."""
    h = _admin(client)
    client.post("/api/items", json={"name": "Organic Virgin Coconut Oil"}, headers=h)  # catalogued, zero stock
    _recipe(client, h, "Curry", ["coconut oil"])
    assert client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"] == []


def test_single_word_discovery_requires_explicit_review_without_counting_stock(
    client: httpx.Client, monkeypatch
) -> None:
    """A token hit offers a question, never proof that banana pepper IS pepper."""
    from modules.automation import culinary_service

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Read/disabled review must not call the classifier")

    monkeypatch.setattr(culinary_service, "evaluate_culinary", forbidden)
    h = _admin(client)
    shelf = _stock(client, h, "Mild Banana Pepper Rings")
    recipe = _recipe(client, h, "Salad", ["pepper"])
    availability_url = f"/api/recipes/{recipe['id']}/availability"
    before = client.get(availability_url, headers=h).json()
    ingredient = before["ingredients"][0]
    assert ingredient["status"] == "missing" and float(ingredient["on_hand"]) == 0
    product_before = client.get(f"/api/items/{shelf['id']}", headers=h).json()
    target_before = client.get(f"/api/items/{ingredient['item_id']}", headers=h).json()

    for _ in range(2):
        matches = client.get("/api/recipes/ingredient-matches", headers=h).json()["matches"]
        assert len(matches) == 1
        assert matches[0]["item_name"] == "pepper"
        assert matches[0]["recipe_id"] == recipe["id"]
        assert [(candidate["item_id"], candidate["item_name"]) for candidate in matches[0]["candidates"]] == [
            (shelf["id"], "Mild Banana Pepper Rings")
        ]
        assert client.get(availability_url, headers=h).json() == before

    # Deliberate evaluation leaves an unresolved manual question with raw names;
    # the default-disabled provider never manufactures an equivalence decision.
    response = client.post(
        "/api/recipes/ingredient-matches/evaluate",
        headers=h,
        json={"recipe_id": recipe["id"], "ingredient_id": ingredient["ingredient_id"]},
    )
    assert response.status_code == 200, response.text
    review = response.json()
    assert not review["policy"]["enabled"] and review["provider_requests"] == 0
    assert review["requested_name"] == "pepper"
    candidate = review["results"][0]
    assert candidate["product_name"] == "Mild Banana Pepper Rings"
    assert candidate["label"] == "insufficient_evidence" and not candidate["can_confirm"]
    rejected = client.post(
        f"/api/recipes/ingredient-matches/reviews/{review['id']}/reject",
        headers=h,
        json={key: review[key] for key in ("revision", "evidence_hash", "confirmation_token")},
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.get(f"/api/items/{shelf['id']}", headers=h).json() == product_before
    assert client.get(f"/api/items/{ingredient['item_id']}", headers=h).json() == target_before
    assert client.get(availability_url, headers=h).json() == before
    assert not calls


def test_archived_recipe_target_does_not_hide_unrelated_pending_matches(client: httpx.Client, monkeypatch) -> None:
    from test_ranked_ingredient_matching import truth

    from modules.automation import culinary_service

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Pending reads must not call the classifier")

    monkeypatch.setattr(culinary_service, "evaluate_culinary", forbidden)
    h = _admin(client)
    _stock(client, h, "Garbanzo beans")
    archived_recipe = _recipe(client, h, "Archived target recipe", ["chickpeas"])
    target = client.get(f"/api/recipes/{archived_recipe['id']}/availability", headers=h).json()["ingredients"][0]
    archived = client.delete(f"/api/items/{target['item_id']}", headers=h)
    assert archived.status_code == 200 and not archived.json()["is_active"]
    shelf = _stock(client, h, "Organic Virgin Coconut Oil")
    valid_recipe = _recipe(client, h, "Valid unrelated recipe", ["coconut oil"])
    original = truth()

    for _ in range(2):
        response = client.get("/api/recipes/ingredient-matches", headers=h)
        assert response.status_code == 200, response.text
        matches = response.json()["matches"]
        assert len(matches) == 1
        assert matches[0]["recipe_id"] == valid_recipe["id"]
        assert matches[0]["item_name"] == "coconut oil"
        assert [candidate["item_id"] for candidate in matches[0]["candidates"]] == [shelf["id"]]
    assert truth() == original
    assert not calls


def test_pending_discovery_misses_are_deduplicated_only_within_one_request(client: httpx.Client, monkeypatch) -> None:
    from collections import Counter

    from test_ranked_ingredient_matching import truth

    from modules.automation import culinary_service
    from modules.catalog.services import ranked_match_candidates

    h = _admin(client)
    _stock(client, h, "Organic Virgin Coconut Oil")
    for number in range(4):
        _recipe(client, h, f"Repeated recipe {number}", ["coconut oil", "tamarind paste", "chickpeas"])
    # Read concrete item IDs through the real public catalog API.
    catalog = client.get("/api/items", headers=h).json()["items"]
    ids = {item["name"]: item["id"] for item in catalog}
    archived = client.delete(f"/api/items/{ids['chickpeas']}", headers=h)
    assert archived.status_code == 200
    calls = []
    real_discover = ranked_match_candidates.discover

    def tracked_discovery(db, item_id, **kwargs):
        calls.append(str(item_id))
        return real_discover(db, item_id, **kwargs)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Pending discovery must not invoke a classifier")

    monkeypatch.setattr(ranked_match_candidates, "discover", tracked_discovery)
    monkeypatch.setattr(culinary_service, "evaluate_culinary", forbidden)
    before = truth()
    response = client.get("/api/recipes/ingredient-matches", headers=h)
    assert response.status_code == 200, response.text
    matches = response.json()["matches"]
    assert [match["item_name"] for match in matches] == ["coconut oil"]
    assert matches[0]["recipe_names"] == [f"Repeated recipe {number}" for number in range(4)]
    expected = {ids[name]: 1 for name in ("coconut oil", "tamarind paste", "chickpeas")}
    assert Counter(calls) == expected
    assert truth() == before

    # The cache belongs to one GET: new stock is discoverable on the next GET.
    _stock(client, h, "Organic Tamarind Paste")
    next_before = truth()
    response = client.get("/api/recipes/ingredient-matches", headers=h)
    assert response.status_code == 200, response.text
    matches = response.json()["matches"]
    assert {match["item_name"] for match in matches} == {"coconut oil", "tamarind paste"}
    assert all(match["recipe_names"] == [f"Repeated recipe {number}" for number in range(4)] for match in matches)
    assert Counter(calls) == {item_id: 2 for item_id in expected}
    assert truth() == next_before
