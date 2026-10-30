"""Shared parsed/structured import and read-only review contracts."""
from uuid import uuid4

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Item, Recipe, Inventory, RecipeIngredient
from modules.automation.recipe_import_parser import parse_recipe_text


def counts():
    with SessionLocal() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Item, Recipe, RecipeIngredient, Inventory))


def test_mixed_fractions_and_optional_context():
    rows = parse_recipe_text("Cake\nIngredients:\n1 1/2 cups flour\n1/2 cup milk\nsalt, to taste\n1 pinch pepper (optional)\nInstructions:\nMix.")["ingredients"]
    assert [(r["name"], r["quantity"], r["unit"], r["optional"]) for r in rows] == [
        ("flour", 1.5, "cup", False), ("milk", .5, "cup", False),
        ("salt", 1, "each", True), ("pepper", 1, "pinch", True)]
    assert rows[0]["raw_line"] == "1 1/2 cups flour"
    assert rows[2]["raw_line"] == "salt, to taste"


def test_parse_and_validate_read_only_and_same_identity(client):
    item = client.post("/api/items", json={"name": "Egg"}).json()
    before = counts()
    raw = "Egg dish\nIngredients:\n2 eggs\n1 pinch salt, to taste\nInstructions:\nCook."
    parsed = client.post("/api/recipes/parse-text", json={"raw_text": raw})
    assert parsed.status_code == 200, parsed.text
    rows = parsed.json()["ingredients"]
    assert rows[0]["item_id"] == item["id"]
    assert rows[1]["unit_resolution"] == "flagged"
    specs = [{k: r[k] for k in ("name", "quantity", "unit", "raw_line", "optional")} for r in rows]
    preview = client.post("/api/recipes/validate", json={"name": "Egg dish", "ingredients": specs}).json()
    assert preview["ingredients"][0]["item_id"] == item["id"]
    assert preview["ingredients"][1]["raw_line"] == rows[1]["raw_line"]
    assert preview["ingredients"][1]["optional"] is True
    assert counts() == before
    a = client.post("/api/recipes/ingest-parsed", json={"title": "Parsed", "ingredients": rows, "source_text": raw})
    assert a.status_code == 200, a.text
    b = client.post("/api/recipes", json={"name": "Structured", "ingredients": specs, "source": "text", "source_text": raw})
    assert b.status_code == 200, b.text
    ad = client.get(f"/api/recipes/{a.json()['recipe_id']}").json()
    bd = client.get(f"/api/recipes/{b.json()['id']}").json()
    assert {r["item_id"] for r in ad["ingredients"]} == {r["item_id"] for r in bd["ingredients"]}
    assert counts()[0] == 2
    assert ad["recipe"]["source_text"] == bd["recipe"]["source_text"] == raw
    assert next(r for r in ad["ingredients"] if r["item_name"] == "salt")["optional"] is True
    assert a.json()["flagged_units"] == b.json()["flagged_units"]
    salt = next(r for r in client.get("/api/items").json()["items"] if r["name"] == "salt")
    assert salt["canonical_provenance"] == "generated"
    assert salt["canonical_version"] == 2


def test_new_alias_duplicates_preview_matches_commit_and_modes_distinct(client):
    specs = [
        {"name": "Egg", "quantity": 1, "unit": "each", "optional": True},
        {"name": "Eggs", "quantity": 2, "unit": "each"},
        {"name": "Egg", "quantity": 1, "unit": "each", "quantity_mode": "package"},
        {"name": "Milk", "quantity": 1, "unit": "cups"},
        {"name": "Milk", "quantity": 2, "unit": "tablespoons"},
        {"name": "Milk", "quantity": 100, "unit": "g"},
    ]
    preview = client.post("/api/recipes/validate", json={"name": "Merge", "ingredients": specs}).json()
    assert preview["would_create_items"] == ["Egg", "Milk"]
    assert len(preview["merged_ingredients"]) == 4
    assert preview["merged_ingredients"][0]["quantity"] == 3
    assert preview["merged_ingredients"][0]["optional"] is False
    created = client.post("/api/recipes", json={"name": "Merge", "ingredients": specs})
    assert created.status_code == 200, created.text
    actual = client.get(f"/api/recipes/{created.json()['id']}").json()["ingredients"]
    assert len(actual) == 4
    assert sorted((r["item_name"], float(r["quantity_required"]), r["unit"], r["optional"]) for r in actual) == sorted((r["name"], r["quantity"], r["unit"], r["optional"]) for r in preview["merged_ingredients"])
    assert counts()[0] == 2
    assert {r["quantity_mode"] for r in actual if r["item_name"] == "Egg"} == {"amount", "package"}


def test_bad_later_id_or_unresolved_name_has_no_partial_writes(client):
    for path in ("/api/recipes", "/api/recipes/ingest-parsed"):
        rows = [{"name": "New flour", "quantity": 1, "unit": "cup", "raw_line": "1 cup New flour"},
                {"item_id": str(uuid4()), "name": "Missing", "raw_line": "Missing"}]
        payload = {"title" if path.endswith("ingest-parsed") else "name": "Invalid", "ingredients": rows}
        before = counts()
        result = client.post(path, json=payload)
        assert result.status_code == 400, result.text
        assert counts() == before
        rows[1].pop("item_id")
        payload["create_missing_items"] = False
        assert client.post(path, json=payload).status_code == 400
        assert counts() == before


def test_structured_mixed_fraction_and_source_preserved_on_edit(client):
    created = client.post("/api/recipes", json={"name": "Cake", "source": "document", "source_filename": "cake.pdf", "source_text": "1 1/2 cups flour", "ingredients": [{"name": "1 1/2 cups flour"}]})
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    detail = client.get(f"/api/recipes/{rid}").json()
    assert detail["ingredients"][0]["item_name"] == "flour"
    assert float(detail["ingredients"][0]["quantity_required"]) == 1.5
    assert detail["ingredients"][0]["unit"] == "cup"
    assert client.put(f"/api/recipes/{rid}", json={"name": "Edited"}).status_code == 200
    detail = client.get(f"/api/recipes/{rid}").json()
    assert detail["recipe"]["source_text"] == "1 1/2 cups flour"
    assert detail["recipe"]["source_filename"] == "cake.pdf"


def test_ambiguous_words_and_semantics_not_rewritten(client):
    rows = client.post("/api/recipes/parse-text", json={"raw_text": "Demo\nIngredients:\n2 eggs\nStir well\nsalt and pepper\nInstructions:\nCook."}).json()["ingredients"]
    assert [(r["name"], r["unit"]) for r in rows] == [("eggs", "each"), ("Stir well", "each"), ("salt and pepper", "each")]
    assert counts() == (0, 0, 0, 0)


def test_empty_context_line_cannot_shift_resolved_identity(client):
    item = client.post('/api/items', json={'name': 'Flour'}).json()
    response = client.post('/api/recipes/parse-text', json={'raw_text': 'Dish\nIngredients\nto taste\n1 cup Flour'})
    assert response.status_code == 200, response.text
    assert [(r['name'], r['item_id']) for r in response.json()['ingredients']] == [('Flour', item['id'])]


def test_storage_quantity_bounds_fail_before_writes(client):
    for amount in (0.0001, 1e100):
        for path in ('/api/recipes/validate', '/api/recipes', '/api/recipes/ingest-parsed'):
            key = 'title' if path.endswith('ingest-parsed') else 'name'
            result = client.post(path, json={key: 'Invalid', 'ingredients': [{'name': 'Flour', 'raw_line': 'Flour', 'quantity': amount}]})
            assert result.status_code == 422, result.text
            assert counts() == (0, 0, 0, 0)
    specs = [{'name': 'Flour', 'quantity': 600000000}, {'name': 'Flour', 'quantity': 600000000}]
    preview = client.post('/api/recipes/validate', json={'name': 'Overflow', 'ingredients': specs}).json()
    assert preview['valid'] is False
    assert 'merged quantity' in preview['errors'][0]
    assert client.post('/api/recipes', json={'name': 'Overflow', 'ingredients': specs}).status_code == 400
    assert counts() == (0, 0, 0, 0)


def test_invalid_parsed_quantity_returns_client_error_without_writes(client, monkeypatch):
    text = 'Dish\nIngredients\n1/10000 tsp salt'
    result = client.post('/api/recipes/parse-text', json={'raw_text': text})
    assert result.status_code == 422, result.text
    assert 'need correction' in result.json()['detail']
    monkeypatch.setattr('modules.planning.api.recipes.document_bytes_to_text', lambda *args, **kwargs: text)
    result = client.post('/api/recipes/parse-image', files={'file': ('small.txt', b'synthetic', 'text/plain')})
    assert result.status_code == 422, result.text
    assert counts() == (0, 0, 0, 0)


def test_structured_raw_lines_retained_when_full_document_omitted(client):
    result = client.post('/api/recipes', json={'name': 'Toast', 'ingredients': [{'name': 'Bread', 'quantity': 2, 'raw_line': '2 slices Bread'}]})
    assert result.status_code == 200, result.text
    detail = client.get(f"/api/recipes/{result.json()['id']}").json()
    assert detail['recipe']['source_text'] == '2 slices Bread'


def test_package_duplicates_add_counts_without_converting_weight_labels(client):
    specs = [
        {'name': 'Pork roast', 'quantity': 1, 'unit': 'g', 'quantity_mode': 'package', 'optional': True},
        {'name': 'Pork roast', 'quantity': 1, 'unit': 'kg', 'quantity_mode': 'package', 'yield_basis': True},
        {'name': 'Pork roast', 'quantity': 200, 'unit': 'g', 'quantity_mode': 'amount'},
    ]
    preview = client.post('/api/recipes/validate', json={'name': 'Roast', 'ingredients': specs}).json()
    assert len(preview['merged_ingredients']) == 2
    assert preview['merged_ingredients'][0]['quantity'] == 2
    assert preview['merged_ingredients'][0]['optional'] is False
    assert preview['ingredients'][0]['quantity_mode'] == 'package'
    assert preview['ingredients'][0]['yield_basis'] is False
    assert preview['ingredients'][1]['yield_basis'] is True
    assert preview['merged_ingredients'][0]['quantity_mode'] == 'package'
    assert preview['merged_ingredients'][0]['yield_basis'] is True
    assert preview['merged_ingredients'][1]['quantity_mode'] == 'amount'
    assert preview['merged_ingredients'][1]['yield_basis'] is False
    created = client.post('/api/recipes', json={'name': 'Roast', 'ingredients': specs})
    assert created.status_code == 200, created.text
    actual = client.get(f"/api/recipes/{created.json()['id']}").json()['ingredients']
    assert len(actual) == 2
    package = next(row for row in actual if row['quantity_mode'] == 'package')
    assert float(package['quantity_required']) == 2
    assert package['yield_basis'] is True
    assert package['optional'] is False
    assert float(next(row for row in actual if row['quantity_mode'] == 'amount')['quantity_required']) == 200
