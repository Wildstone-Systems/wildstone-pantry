"""External identity (upsert), bulk import, and dry-run validation.

Three gaps an agent hit importing a recipe feed: re-running the feed duplicated
every recipe, importing N recipes cost N round-trips with no per-recipe error
reporting, and there was no way to see what normalisation would do without
writing it.
"""

from __future__ import annotations

import httpx


def _mealdb(name: str, external_id: str = "52802", **extra) -> dict:
    payload = {
        "name": name,
        "external_source": {"source": "themealdb", "id": external_id},
        "ingredients": [{"name": "chicken thighs", "quantity": 500, "unit": "g"}],
    }
    payload.update(extra)
    return payload


# --- Item 4: stable external identity -------------------------------------------------


def test_reimporting_the_same_external_id_updates_instead_of_duplicating(client: httpx.Client) -> None:
    first = client.post("/api/recipes", json=_mealdb("Chicken Handi"))
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True

    second = client.post("/api/recipes", json=_mealdb("Chicken Handi (revised)"))
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]

    recipes = client.get("/api/recipes").json()
    assert len([r for r in recipes if r["external_id"] == "52802"]) == 1
    assert [r for r in recipes if r["external_id"] == "52802"][0]["name"] == "Chicken Handi (revised)"


def test_external_identity_is_scoped_by_source_not_id_alone(client: httpx.Client) -> None:
    """Two feeds can legitimately both use id "1"."""
    client.post(
        "/api/recipes",
        json={"name": "From A", "external_source": {"source": "feed-a", "id": "1"}},
    )
    client.post(
        "/api/recipes",
        json={"name": "From B", "external_source": {"source": "feed-b", "id": "1"}},
    )
    assert len(client.get("/api/recipes").json()) == 2


def test_recipes_without_external_identity_never_collide(client: httpx.Client) -> None:
    """NULLs must not conflict under the unique constraint."""
    for _ in range(3):
        assert client.post("/api/recipes", json={"name": "Toast"}).status_code == 200
    assert len(client.get("/api/recipes").json()) == 3


def test_upsert_replaces_ingredients_rather_than_appending(client: httpx.Client) -> None:
    client.post("/api/recipes", json=_mealdb("Curry"))
    resp = client.post(
        "/api/recipes",
        json=_mealdb(
            "Curry",
            ingredients=[
                {"name": "chicken thighs", "quantity": 600, "unit": "g"},
                {"name": "yogurt", "quantity": 100, "unit": "g"},
            ],
        ),
    )
    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert {i["item_name"] for i in got["ingredients"]} == {"chicken thighs", "yogurt"}
    assert float([i for i in got["ingredients"] if i["item_name"] == "chicken thighs"][0]["quantity_required"]) == 600.0


def test_external_url_round_trips(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Ragu",
            "external_source": {"source": "themealdb", "id": "52770", "url": "https://themealdb.com/meal/52770"},
        },
    )
    assert resp.json()["external_url"] == "https://themealdb.com/meal/52770"
    assert resp.json()["external_source"] == "themealdb"


def test_metadata_only_update_does_not_orphan_external_identity(client: httpx.Client) -> None:
    """A PUT that omits the block must not clear the link to the feed."""
    created = client.post("/api/recipes", json=_mealdb("Chicken Handi")).json()
    client.put(f"/api/recipes/{created['id']}", json={"name": "Renamed", "servings": 4})

    again = client.post("/api/recipes", json=_mealdb("Chicken Handi"))
    assert again.json()["id"] == created["id"]
    assert again.json()["created"] is False


# --- Item 5: bulk import --------------------------------------------------------------


def test_bulk_import_creates_every_recipe_and_reports_counts(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes/bulk",
        json={
            "recipes": [
                _mealdb("Chicken Handi", "52802"),
                _mealdb("Beef Rendang", "52803"),
                {"name": "Toast", "ingredients": [{"name": "bread", "quantity": 2, "unit": "each"}]},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created"], body["updated"], body["failed"]) == (3, 0, 0)
    assert [r["status"] for r in body["results"]] == ["created"] * 3
    assert len(client.get("/api/recipes").json()) == 3


def test_bulk_import_reruns_as_updates_not_duplicates(client: httpx.Client) -> None:
    batch = {"recipes": [_mealdb("Chicken Handi", "52802"), _mealdb("Beef Rendang", "52803")]}
    client.post("/api/recipes/bulk", json=batch)
    second = client.post("/api/recipes/bulk", json=batch).json()

    assert (second["created"], second["updated"], second["failed"]) == (0, 2, 0)
    assert len(client.get("/api/recipes").json()) == 2


def test_one_bad_recipe_does_not_cost_the_rest_of_the_batch(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes/bulk",
        json={
            "recipes": [
                {"name": "Good One", "ingredients": [{"name": "salt", "quantity": 1, "unit": "tsp"}]},
                # create_missing_items off + an unknown name = a hard error for this entry.
                {
                    "name": "Bad One",
                    "create_missing_items": False,
                    "ingredients": [{"name": "nonexistent unobtainium", "quantity": 1, "unit": "g"}],
                },
                {"name": "Another Good One", "ingredients": [{"name": "pepper", "quantity": 1, "unit": "tsp"}]},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created"], body["failed"]) == (2, 1)

    failed = [r for r in body["results"] if r["status"] == "error"]
    assert len(failed) == 1
    assert failed[0]["index"] == 1
    assert failed[0]["name"] == "Bad One"
    assert "unobtainium" in failed[0]["error"]

    assert {r["name"] for r in client.get("/api/recipes").json()} == {"Good One", "Another Good One"}


def test_bulk_import_surfaces_flagged_units_per_recipe(client: httpx.Client) -> None:
    body = client.post(
        "/api/recipes/bulk",
        json={
            "recipes": [
                {"name": "Aioli", "ingredients": [{"name": "garlic", "quantity": 4, "unit": "clove"}]},
                {"name": "Rice", "ingredients": [{"name": "rice", "quantity": 200, "unit": "g"}]},
            ]
        },
    ).json()

    assert body["results"][0]["flagged_units"][0]["unit"] == "clove"
    assert body["results"][0]["flagged_units"][0]["suggestion"] == "each"
    assert body["results"][1]["flagged_units"] == []


def test_bulk_import_rejects_an_empty_or_oversized_batch(client: httpx.Client) -> None:
    assert client.post("/api/recipes/bulk", json={"recipes": []}).status_code == 422
    oversized = {"recipes": [{"name": f"R{i}"} for i in range(201)]}
    assert client.post("/api/recipes/bulk", json=oversized).status_code == 422


# --- Item 8: dry-run validation -------------------------------------------------------


def test_validate_writes_nothing(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes/validate",
        json={"name": "Aioli", "ingredients": [{"name": "garlic", "quantity": 4, "unit": "clove"}]},
    )
    assert resp.status_code == 200, resp.text

    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json()["items"] == []


def test_validate_reports_folded_units_flags_and_new_items(client: httpx.Client) -> None:
    client.post("/api/items", json={"name": "Olive Oil"})
    body = client.post(
        "/api/recipes/validate",
        json={
            "name": "Aioli",
            "ingredients": [
                {"name": "Olive Oil", "quantity": 2, "unit": "Tbs"},
                {"name": "garlic", "quantity": 4, "unit": "clove"},
            ],
        },
    ).json()

    assert body["valid"] is True  # a flagged unit is a warning, not a rejection
    assert body["would_create_items"] == ["garlic"]

    by_index = {line["index"]: line for line in body["ingredients"]}
    assert by_index[0]["resolution"] == "matched"
    assert by_index[0]["unit"] == "tbsp"  # the fold is previewed
    assert by_index[0]["unit_resolution"] == "ok"
    assert by_index[1]["resolution"] == "would_create"
    assert by_index[1]["unit_resolution"] == "flagged"
    assert by_index[1]["suggestion"] == "each"

    assert [f["unit"] for f in body["flagged_units"]] == ["clove"]


def test_validate_previews_the_merge_of_duplicate_lines(client: httpx.Client) -> None:
    body = client.post(
        "/api/recipes/validate",
        json={
            "name": "Stir Fry",
            "ingredients": [
                {"name": "sesame oil", "quantity": 1, "unit": "tbsp"},
                {"name": "soy sauce", "quantity": 2, "unit": "tbsp"},
                {"name": "sesame oil", "quantity": 2, "unit": "tbsp"},
            ],
        },
    ).json()

    merged = {row["name"]: row for row in body["merged_ingredients"]}
    assert len(body["merged_ingredients"]) == 2
    assert merged["sesame oil"]["quantity"] == 3.0
    assert merged["sesame oil"]["from_indexes"] == [0, 2]


def test_validate_does_not_merge_unrelated_new_items(client: httpx.Client) -> None:
    """Distinct not-yet-created items have no id; they must not collapse together."""
    body = client.post(
        "/api/recipes/validate",
        json={
            "name": "Salad",
            "ingredients": [
                {"name": "romaine", "quantity": 1, "unit": "each"},
                {"name": "cucumber", "quantity": 1, "unit": "each"},
            ],
        },
    ).json()

    assert len(body["merged_ingredients"]) == 2
    assert body["would_create_items"] == ["romaine", "cucumber"]


def test_validate_marks_a_payload_that_would_fail_as_invalid(client: httpx.Client) -> None:
    body = client.post(
        "/api/recipes/validate",
        json={
            "name": "Impossible",
            "create_missing_items": False,
            "ingredients": [{"name": "nonexistent unobtainium", "quantity": 1, "unit": "g"}],
        },
    ).json()

    assert body["valid"] is False
    assert body["ingredients"][0]["resolution"] == "unresolved"
    assert "unobtainium" in body["errors"][0]


def test_validate_reports_whether_the_write_would_upsert(client: httpx.Client) -> None:
    fresh = client.post("/api/recipes/validate", json=_mealdb("Chicken Handi")).json()
    assert fresh["would_upsert"] is False
    assert fresh["existing_recipe_id"] is None

    created = client.post("/api/recipes", json=_mealdb("Chicken Handi")).json()

    again = client.post("/api/recipes/validate", json=_mealdb("Chicken Handi")).json()
    assert again["would_upsert"] is True
    assert again["existing_recipe_id"] == created["id"]
