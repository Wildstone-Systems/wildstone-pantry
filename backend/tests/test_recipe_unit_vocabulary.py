"""The import path folds unit spellings, and *reports* the ones it can't (ADR 0017 §3).

PantryOS already had a closed recipe unit vocabulary and a strict editor path. What
was missing was honesty on the agent/import path: `POST /api/recipes` normalised what
it recognised, kept what it didn't, and told the caller nothing — so `clove`, `tblsp`
and a bare `T` reached the database looking exactly as successful as `tbsp`. These
tests pin the fold-or-flag contract an importing agent needs to act on.
"""

from __future__ import annotations

import httpx

from modules.catalog.services.units import normalize_unit, suggest_unit, to_recipe_unit


def test_tablespoon_contractions_fold_to_one_canonical_unit() -> None:
    """The spellings that made one unit look like four."""
    for spelling in ("tbsp", "Tbsp.", "tbs", "tbls", "tblsp", "tblspn", "TABLESPOONS", "tablespoonful"):
        assert to_recipe_unit(spelling) == "tbsp", spelling


def test_abbreviation_periods_and_whitespace_are_stripped() -> None:
    assert to_recipe_unit("fl. oz.") == "floz"
    assert to_recipe_unit("  LBS  ") == "lb"
    assert normalize_unit("Kilos") == "kg"


def test_bare_t_stays_ambiguous_and_is_never_guessed() -> None:
    """`T` (tablespoon) and `t` (teaspoon) fold together under case-folding and differ
    3x. ADR 0017 §3 disallows both rather than pick a side."""
    assert to_recipe_unit("T") is None
    assert to_recipe_unit("t") is None


def test_suggest_unit_offers_each_for_countable_things_but_not_for_amounts() -> None:
    assert suggest_unit("clove") == "each"
    assert suggest_unit("slices") == "each"
    # An amount we genuinely cannot pin down gets no fabricated suggestion.
    assert suggest_unit("pinch") is None
    assert suggest_unit("handful") is None
    # A recognised unit is not a flag at all.
    assert suggest_unit("cup") is None


def test_create_recipe_flags_unknown_unit_and_still_imports(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Garlic Confit",
            "ingredients": [
                {"name": "garlic", "quantity": 12, "unit": "clove"},
                {"name": "olive oil", "quantity": 2, "unit": "cup"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The recipe imported — the flag path does not fail the whole payload.
    flagged = body["flagged_units"]
    assert len(flagged) == 1
    assert flagged[0]["unit"] == "clove"
    assert flagged[0]["item_name"] == "garlic"
    assert flagged[0]["suggestion"] == "each"

    got = client.get(f"/api/recipes/{body['id']}").json()
    assert {i["item_name"] for i in got["ingredients"]} == {"garlic", "olive oil"}


def test_create_recipe_reports_no_flags_when_every_unit_resolves(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Vinaigrette",
            "ingredients": [
                {"name": "olive oil", "quantity": 3, "unit": "Tbs"},
                {"name": "red wine vinegar", "quantity": 1, "unit": "tablespoon"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["flagged_units"] == []

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert {i["unit"] for i in got["ingredients"]} == {"tbsp"}


def test_editor_path_still_rejects_a_non_vocabulary_unit(client: httpx.Client) -> None:
    """The strict primitive is unchanged: the picker only offers vocabulary units,
    so a bad token there is a client bug, not an import to salvage."""
    item = client.post("/api/items", json={"name": "Garlic"}).json()
    recipe = client.post("/api/recipes", json={"name": "Aioli"}).json()
    resp = client.put(
        f"/api/recipes/{recipe['id']}/ingredients",
        json=[{"item_id": item["id"], "quantity_required": 2, "unit": "clove"}],
    )
    assert resp.status_code == 422, resp.text


def test_unit_vocab_endpoint_covers_every_unit_the_importer_can_produce(client: httpx.Client) -> None:
    """Anything selectable is convertible — so anything the fold produces must be
    something the picker offers, or the editor can't display an imported recipe."""
    vocab = {u["value"] for u in client.get("/api/recipes/unit-vocab").json()["units"]}
    for spelling in ("tbs", "tblsp", "fl. oz.", "kilos", "LBS", "teaspoons", "cups"):
        assert to_recipe_unit(spelling) in vocab, spelling


def test_duplicate_ingredient_lines_merge_and_sum(client: httpx.Client) -> None:
    """A recipe that lists oil twice should leave one row, not two."""
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Stir Fry",
            "ingredients": [
                {"name": "sesame oil", "quantity": 1, "unit": "tbsp"},
                {"name": "soy sauce", "quantity": 2, "unit": "tbsp"},
                {"name": "sesame oil", "quantity": 2, "unit": "tbsp"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    by_name = {i["item_name"]: i for i in got["ingredients"]}
    assert len(got["ingredients"]) == 2
    assert float(by_name["sesame oil"]["quantity_required"]) == 3.0


def test_duplicate_lines_in_different_units_convert_before_summing(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Bread",
            "ingredients": [
                {"name": "bread flour", "quantity": 500, "unit": "g"},
                {"name": "bread flour", "quantity": 0.25, "unit": "kg"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert len(got["ingredients"]) == 1
    assert float(got["ingredients"][0]["quantity_required"]) == 750.0


def test_cross_dimension_duplicates_stay_separate_rather_than_guess(client: httpx.Client) -> None:
    """Summing 200 g and 1 cup needs the item's density. ADR 0017 forbids guessing,
    so the lines stay separate instead of silently combining wrong."""
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Ambiguous Cake",
            "ingredients": [
                {"name": "cake flour", "quantity": 200, "unit": "g"},
                {"name": "cake flour", "quantity": 1, "unit": "cup"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert len(got["ingredients"]) == 2
    assert {i["unit"] for i in got["ingredients"]} == {"g", "cup"}


def test_merged_line_is_optional_only_when_every_source_line_was(client: httpx.Client) -> None:
    resp = client.post(
        "/api/recipes",
        json={
            "name": "Curry",
            "ingredients": [
                {"name": "coriander", "quantity": 1, "unit": "tsp", "optional": True},
                {"name": "coriander", "quantity": 1, "unit": "tsp", "optional": False},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert len(got["ingredients"]) == 1
    assert got["ingredients"][0]["optional"] is False


def test_post_accepts_quantity_required_the_name_get_reads_it_back_under(client: httpx.Client) -> None:
    """Round-tripping a GET body into a POST used to fall back to the 1.0 default."""
    resp = client.post(
        "/api/recipes",
        json={"name": "Rice", "ingredients": [{"name": "basmati rice", "quantity_required": 400, "unit": "g"}]},
    )
    assert resp.status_code == 200, resp.text

    got = client.get(f"/api/recipes/{resp.json()['id']}").json()
    assert float(got["ingredients"][0]["quantity_required"]) == 400.0
