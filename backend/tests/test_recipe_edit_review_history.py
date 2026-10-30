"""Retained recipe lines keep review receipts with real SQLite FK enforcement."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from test_reviewed_ingredient_matching import confirm, create, setup

from app.db.session import SessionLocal, engine
from modules.planning.models.ingredient_match_review import IngredientMatchReview, IngredientMatchScope


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "history-edit@example.com", "password": "HistoryEdit987!"})
    response = client.post("/api/auth/login", json={"email": "history-edit@example.com", "password": "HistoryEdit987!"})
    return {"Authorization": "Bearer " + response.json()["token"]}


@pytest.fixture(autouse=True)
def enforced_foreign_keys():
    def enforce(connection, _record, _proxy):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "checkout", enforce)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    try:
        yield
    finally:
        event.remove(engine, "checkout", enforce)
        engine.dispose()


def get(client, admin, recipe_id):
    response = client.get(f"/api/recipes/{recipe_id}", headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def editor_payload(snapshot, **changes):
    recipe = snapshot["recipe"]
    return {
        "name": recipe["name"],
        "instructions": recipe["instructions"],
        "servings": recipe["servings"],
        "ingredients": [
            {
                "item_id": line["item_id"],
                "quantity": float(line["quantity_required"]),
                "unit": line["unit"],
                "optional": line["optional"],
                "yield_basis": line["yield_basis"],
                "quantity_mode": line["quantity_mode"],
            }
            for line in snapshot["ingredients"]
        ],
        **changes,
    }


def test_ordinary_editor_put_preserves_confirmed_receipt_scope_and_line_id(client, admin):
    data = setup(client, admin)
    review = create(client, admin, data)
    assert confirm(client, admin, review).status_code == 200
    snapshot = get(client, admin, data[0])
    response = client.put(f"/api/recipes/{data[0]}", headers=admin, json=editor_payload(snapshot))
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        receipt = db.get(IngredientMatchReview, UUID(review["id"]))
        scope = db.get(IngredientMatchScope, UUID(data[1]))
        assert receipt is not None and receipt.status == "confirmed"
        assert scope is not None and scope.current_review_id == receipt.id
    assert get(client, admin, data[0])["ingredients"] == snapshot["ingredients"]
    history = client.get(f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-reviews", headers=admin)
    assert history.status_code == 200
    assert history.json()["reviews"][0]["current_choice"]


def test_changed_line_and_recipe_keep_history_but_stale_old_pending_binding(client, admin):
    data = setup(client, admin)
    receipt = create(client, admin, data)
    assert confirm(client, admin, receipt).status_code == 200
    pending = create(client, admin, data)
    snapshot = get(client, admin, data[0])
    payload = editor_payload(snapshot, instructions="A different preparation step")
    payload["ingredients"][0]["quantity"] = 3
    response = client.put(f"/api/recipes/{data[0]}", headers=admin, json=payload)
    assert response.status_code == 200, response.text
    assert get(client, admin, data[0])["ingredients"][0]["ingredient_id"] == data[1]
    assert confirm(client, admin, pending).status_code == 409
    history = client.get(f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-reviews", headers=admin).json()["reviews"]
    assert {row["id"] for row in history} == {receipt["id"], pending["id"]}
    assert next(row for row in history if row["id"] == receipt["id"])["current_choice"]


def test_dedicated_ingredient_put_preserves_duplicate_ids_before_matching_changed_sibling(client, admin):
    data = setup(client, admin)
    url = f"/api/recipes/{data[0]}/ingredients"
    initial = [
        {"item_id": data[2], "quantity_required": 1, "unit": "g", "yield_basis": True},
        {"item_id": data[2], "quantity_required": 2, "unit": "g", "quantity_mode": "package"},
        {"item_id": data[2], "quantity_required": 3, "unit": "ml", "optional": True},
    ]
    assert client.put(url, headers=admin, json=initial).status_code == 200
    before = get(client, admin, data[0])["ingredients"]
    ids = {(row["unit"], float(row["quantity_required"])): row["ingredient_id"] for row in before}
    review = create(client, admin, (data[0], ids[("g", 1)], data[2], data[3]))
    assert confirm(client, admin, review).status_code == 200
    payload = [
        {"item_id": data[2], "quantity_required": 4, "unit": "g"},
        {"item_id": data[2], "quantity_required": 3, "unit": "ml", "optional": True},
        {"item_id": data[2], "quantity_required": 1, "unit": "g"},
    ]
    response = client.put(url, headers=admin, json=payload)
    assert response.status_code == 200, response.text
    after = get(client, admin, data[0])["ingredients"]
    by_quantity = {float(row["quantity_required"]): row for row in after}
    assert by_quantity[1]["ingredient_id"] == ids[("g", 1)] and by_quantity[1]["yield_basis"]
    assert by_quantity[3]["ingredient_id"] == ids[("ml", 3)] and by_quantity[3]["optional"]
    assert by_quantity[4]["ingredient_id"] == ids[("g", 2)] and by_quantity[4]["quantity_mode"] == "package"
    with SessionLocal() as db:
        receipt = db.get(IngredientMatchReview, UUID(review["id"]))
        assert receipt is not None and str(receipt.ingredient_id) == ids[("g", 1)]


def test_invalid_item_rolls_back_and_genuine_removal_cascades_review(client, admin):
    data = setup(client, admin)
    review = create(client, admin, data)
    assert confirm(client, admin, review).status_code == 200
    before = get(client, admin, data[0])["ingredients"]
    url = f"/api/recipes/{data[0]}/ingredients"
    response = client.put(
        url,
        headers=admin,
        json=[
            {"item_id": data[2], "quantity_required": 99, "unit": "each"},
            {"item_id": str(uuid4()), "quantity_required": 1, "unit": "each"},
        ],
    )
    assert response.status_code == 404
    assert get(client, admin, data[0])["ingredients"] == before
    with SessionLocal() as db:
        assert db.get(IngredientMatchReview, UUID(review["id"])) is not None
    added = client.post("/api/items", headers=admin, json={"name": "New retained ingredient"}).json()["id"]
    response = client.put(url, headers=admin, json=[{"item_id": added, "quantity_required": 1, "unit": "each"}])
    assert response.status_code == 200
    after = get(client, admin, data[0])["ingredients"]
    assert len(after) == 1 and after[0]["item_id"] == added and after[0]["ingredient_id"] != data[1]
    with SessionLocal() as db:
        assert db.get(IngredientMatchReview, UUID(review["id"])) is None
        assert db.get(IngredientMatchScope, UUID(data[1])) is None
