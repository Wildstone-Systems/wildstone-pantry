"""Reusable facts via real API/SQLite; fixtures never call a model provider."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select
from test_reviewed_ingredient_matching import binding, confirm, create, setup

from app.db.session import SessionLocal
from app.models import Item, RecipeIngredient
from modules.automation.culinary_questions import context_request, identity_request
from modules.catalog.models import ItemProductEvidence
from modules.catalog.services import match_evidence

FACTS = {
    "identity": "Chicken breast",
    "base_ingredient": "chicken",
    "form": "boneless breast",
    "preparation": "raw",
    "composition": "chicken breast",
}


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "facts@example.com", "password": "FactsPass987!"})
    response = client.post("/api/auth/login", json={"email": "facts@example.com", "password": "FactsPass987!"})
    return {"Authorization": "Bearer " + response.json()["token"]}


def path(item_id):
    return f"/api/items/{item_id}/structured-evidence"


def read(client, admin, item_id):
    response = client.get(path(item_id), headers=admin)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, admin, item_id, facts, expected=None):
    current = expected or read(client, admin, item_id)
    return client.put(
        path(item_id),
        headers=admin,
        json={
            "confirm": True,
            "expected_revision": current["revision"],
            "base_source_hash": current["base_source_hash"],
            "facts": facts,
        },
    )


def test_save_reuse_across_recipes_keeps_catalog_truth_and_provenance(client, admin):
    data = setup(client, admin, product="CHK BRST", target="Chicken breast")
    with SessionLocal() as db:
        row = db.get(Item, UUID(data[3]))
        row.ingredients_text = "Original supplied label"
        row.energy_kcal_100g = 120
        row.nutrition_estimated = True
        db.commit()
        before = {column.name: getattr(row, column.name) for column in Item.__table__.columns}
    response = save(client, admin, data[3], FACTS)
    assert response.status_code == 200, response.text
    stored = response.json()
    assert stored["revision"] == 1 and not stored["stale"] and stored["updated_by"] and stored["updated_at"]
    assert stored["provenance"] == "user_provided"
    for index in range(2):
        if index:
            recipe = client.post("/api/recipes", headers=admin, json={"name": "Second soup"}).json()["id"]
            assert (
                client.put(
                    f"/api/recipes/{recipe}/ingredients",
                    headers=admin,
                    json=[{"item_id": data[2], "quantity_required": 2, "unit": "each"}],
                ).status_code
                == 200
            )
            with SessionLocal() as db:
                ingredient = str(
                    db.scalar(select(RecipeIngredient.id).where(RecipeIngredient.recipe_id == UUID(recipe)))
                )
            data = recipe, ingredient, data[2], data[3]
        review = create(client, admin, data, {})
        assert review["can_confirm"] and review["next_clarification"] is None
        assert review["packet"]["resolved"]["identity"] == FACTS["identity"]
        assert review["packet"]["fact_sources"]["identity"] == "saved_user_provided"
        assert review["packet"]["resolved"]["base_ingredient"] == "chicken"
        product = review["packet"]["product"]
        assert product["ingredients_text"] == "Original supplied label"
        assert float(product["nutrition_values"]["energy_kcal_100g"]) == 120
        assert product["nutrition_estimated"]
        for request in (identity_request(review["packet"]), context_request(review["packet"])):
            state = json.loads(request.state)["evidence"]
            assert state["resolved"]["base_ingredient"] == "chicken"
            assert stored["updated_by"] not in request.state
            assert "updated_at" not in request.state and "base_source_hash" not in request.state
    with SessionLocal() as db:
        row = db.get(Item, UUID(data[3]))
        assert {column.name: getattr(row, column.name) for column in Item.__table__.columns} == before


def test_targeted_question_and_manual_review_facts_do_not_save_metadata(client, admin):
    data = setup(client, admin, product="CHK BRST", target="Chicken breast")
    first = create(client, admin, data, {})
    assert first["next_clarification"]["field"] == "identity" and not first["can_confirm"]
    second = create(client, admin, data, {"identity": "Chicken breast"})
    assert second["next_clarification"]["field"] == "preparation"
    third = create(client, admin, data, {"identity": "Chicken breast", "preparation": "raw"})
    assert third["next_clarification"]["field"] == "form"
    complete = create(client, admin, data, FACTS)
    assert complete["next_clarification"] is None and complete["can_confirm"]
    assert read(client, admin, data[3])["revision"] == 0
    assert (
        client.post(
            "/api/ingredient-match-reviews/" + complete["id"] + "/reject", headers=admin, json=binding(complete)
        ).status_code
        == 200
    )
    assert read(client, admin, data[3])["revision"] == 0


def test_manual_review_override_wins_without_changing_saved_facts(client, admin):
    data = setup(client, admin, product="CHK BRST")
    assert save(client, admin, data[3], FACTS).status_code == 200
    view = create(client, admin, data, {"preparation": "cooked"})
    assert view["packet"]["resolved"]["preparation"] == "cooked"
    assert view["packet"]["fact_sources"]["preparation"] == "user_provided"
    assert view["packet"]["fact_sources"]["identity"] == "saved_user_provided"
    assert read(client, admin, data[3])["facts"]["preparation"] == "raw"


def test_stale_source_ignores_saved_facts_and_rejects_old_source_save(client, admin):
    data = setup(client, admin, product="CHK BRST")
    initial = read(client, admin, data[3])
    assert save(client, admin, data[3], FACTS, initial).status_code == 200
    current = read(client, admin, data[3])
    with SessionLocal() as db:
        item = db.get(Item, UUID(data[3]))
        item.ingredients_text = "x" * 2200 + " changed full source"
        db.commit()
    stale = read(client, admin, data[3])
    assert stale["stale"] and stale["facts"]["identity"] == "Chicken breast"
    review = create(client, admin, data, {})
    assert review["next_clarification"]["field"] == "identity" and not review["can_confirm"]
    assert review["packet"]["product"]["structured_evidence"]["stale"]
    assert save(client, admin, data[3], FACTS, current).status_code == 409
    assert save(client, admin, data[3], FACTS, stale).status_code == 200
    assert create(client, admin, data, {})["can_confirm"]


def test_metadata_changes_invalidate_pending_but_preserve_confirmed_history(client, admin):
    data = setup(client, admin, product="CHK BRST")
    assert save(client, admin, data[3], FACTS).status_code == 200
    confirmed = create(client, admin, data, {})
    assert confirm(client, admin, confirmed).status_code == 200
    pending = create(client, admin, data, {})
    old = read(client, admin, data[3])
    changed = {**FACTS, "preparation": "cooked"}
    assert save(client, admin, data[3], changed).status_code == 200
    assert confirm(client, admin, pending).status_code == 409
    loaded = client.get("/api/ingredient-match-reviews/" + confirmed["id"], headers=admin).json()
    assert loaded["status"] == "confirmed" and loaded["current_choice"] and loaded["stale"]
    assert save(client, admin, data[3], FACTS, old).status_code == 409


def test_unknown_placeholders_omitted_and_explicit_clear_advances_revision(client, admin):
    data = setup(client, admin, product="CHK BRST")
    assert save(client, admin, data[3], {"identity": "unknown", "form": " ", "preparation": "?"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(ItemProductEvidence, UUID(data[3])).facts == {}
    assert not create(client, admin, data, {})["can_confirm"]
    assert save(client, admin, data[3], FACTS).status_code == 200
    previous = read(client, admin, data[3])
    cleared = save(client, admin, data[3], {})
    assert cleared.status_code == 200 and cleared.json()["revision"] == previous["revision"] + 1
    assert not create(client, admin, data, {})["can_confirm"]


@pytest.mark.parametrize(
    "change",
    [
        {"confirm": 1},
        {"confirm": False},
        {"expected_revision": True},
        {"expected_revision": 0.0},
        {"facts": {"nutrition": 123}},
        {"facts": {"recipe_role": "Soup"}},
    ],
)
def test_save_commands_are_closed_and_require_explicit_strict_confirmation(client, admin, change):
    data = setup(client, admin)
    current = read(client, admin, data[3])
    command = {
        "confirm": True,
        "expected_revision": 0,
        "base_source_hash": current["base_source_hash"],
        "facts": FACTS,
        **change,
    }
    assert client.put(path(data[3]), headers=admin, json=command).status_code == 422
    assert read(client, admin, data[3])["revision"] == 0


def test_competing_first_saves_are_serialized_no_absent_row_overwrite(client, admin, monkeypatch):
    data = setup(client, admin, product="CHK BRST")
    current = read(client, admin, data[3])
    barrier = Barrier(2)
    original = match_evidence.item_base_evidence

    def racing(db, item_id, *, lock=False):
        if lock:
            barrier.wait(timeout=10)
        return original(db, item_id, lock=lock)

    monkeypatch.setattr(match_evidence, "item_base_evidence", racing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda facts: save(client, admin, data[3], facts, current), [FACTS, {**FACTS, "preparation": "cooked"}]
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    assert read(client, admin, data[3])["revision"] == 1


def test_source_changed_just_before_locked_admission_rejects_save(client, admin, monkeypatch):
    data = setup(client, admin)
    current = read(client, admin, data[3])
    original = match_evidence.item_base_evidence
    changed = []

    def racing(db, item_id, *, lock=False):
        if lock and not changed:
            with SessionLocal() as other:
                row = other.get(Item, item_id)
                row.brand = "Source changed concurrently"
                other.commit()
            changed.append(True)
        return original(db, item_id, lock=lock)

    monkeypatch.setattr(match_evidence, "item_base_evidence", racing)
    assert save(client, admin, data[3], FACTS, current).status_code == 409
    assert read(client, admin, data[3])["revision"] == 0


def test_permissions_reject_unauthorized_writes(client, admin):
    from app.services.auth import create_user

    data = setup(client, admin)
    current = read(client, admin, data[3])
    command = {"confirm": True, "expected_revision": 0, "base_source_hash": current["base_source_hash"], "facts": FACTS}
    assert client.put(path(data[3]), json=command).status_code == 401
    assert client.put(path(data[3]), headers={**admin, "X-Agent-Key": "fixture"}, json=command).status_code == 403
    with SessionLocal() as db:
        create_user(db, email="facts-member@example.com", password="MemberPass987!", role="member")
    token = client.post(
        "/api/auth/login", json={"email": "facts-member@example.com", "password": "MemberPass987!"}
    ).json()["token"]
    assert client.put(path(data[3]), headers={"Authorization": "Bearer " + token}, json=command).status_code == 403
    assert read(client, admin, data[3])["revision"] == 0


def test_product_evidence_migration_roundtrip_preserves_item_values():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = importlib.import_module("migrations.versions.0062_item_product_evidence")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_food(name TEXT)"))
        connection.execute(text("INSERT INTO existing_food VALUES ('Apple')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "item_product_evidence" in inspect(connection).get_table_names()
            migration.downgrade()
            assert "item_product_evidence" not in inspect(connection).get_table_names()
            migration.upgrade()
        assert connection.scalar(text("SELECT name FROM existing_food")) == "Apple"


@pytest.mark.parametrize("unknown", [None, "unknown", "?"])
def test_explicit_unknown_overrides_saved_fact_only_for_this_review(client, admin, unknown):
    data = setup(client, admin, product="CHK BRST")
    assert save(client, admin, data[3], FACTS).status_code == 200
    review = create(client, admin, data, {"preparation": unknown})
    assert review["packet"]["resolved"]["preparation"] is None
    assert review["packet"]["fact_sources"]["preparation"] == "user_provided"
    assert review["next_clarification"]["field"] == "preparation"
    assert not review["can_compare"] and not review["can_confirm"]
    assert read(client, admin, data[3])["facts"]["preparation"] == "raw"
    assert create(client, admin, data, {})["can_confirm"]
