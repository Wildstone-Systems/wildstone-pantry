"""Real API/isolated SQLite acceptance; classifier answers are transport-free fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from test_reviewed_ingredient_matching import answer, enabled, setup

from app.db.session import SessionLocal
from app.models import Base, Inventory, Item, Recipe, RecipeIngredient
from modules.automation import culinary_service
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.planning.models.ranked_ingredient_review import RankedIngredientReview

BASE = "/api/recipes/ingredient-matches"


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "ranked@example.com", "password": "RankedPass987!"})
    response = client.post("/api/auth/login", json={"email": "ranked@example.com", "password": "RankedPass987!"})
    return {"Authorization": "Bearer " + response.json()["token"]}


def evaluate(client, admin, data):
    response = client.post(BASE + "/evaluate", headers=admin, json={"recipe_id": data[0], "ingredient_id": data[1]})
    assert response.status_code == 200, response.text
    return response.json()


def binding(review):
    return {key: review[key] for key in ("revision", "evidence_hash", "confirmation_token")}


def confirm(client, admin, review, candidate=None, **extra):
    return client.post(
        BASE + f"/reviews/{review['id']}/confirm",
        headers=admin,
        json={
            **binding(review),
            "candidate_item_id": candidate or review["results"][0]["candidate_item_id"],
            "confirm_global_alias": True,
            **extra,
        },
    )


def truth():
    with SessionLocal() as db:
        return {
            table.name: sorted(str(tuple(row)) for row in db.execute(select(table)).all())
            for table in Base.metadata.sorted_tables
            if table.name != "ranked_ingredient_reviews"
        }


def fixture(monkeypatch, choice="c_1", probability=0.8):
    calls = []

    async def classify(db, request, **kwargs):
        assert not await kwargs["stop_requested"]()
        assert "Season the sauce" not in request.state
        calls.append(request)
        return answer(request, choice, probability)

    monkeypatch.setattr(culinary_service, "evaluate_culinary", classify)
    return calls


@pytest.mark.parametrize("product,target", [("Garbanzo beans", "Chickpeas"), ("Lawry's Ground Cumin", "Cumin")])
def test_broader_retrieval_pending_and_prediction_never_count_stock(client, admin, monkeypatch, product, target):
    data = setup(client, admin, product=product, target=target)
    calls = fixture(monkeypatch)
    pending = client.get(BASE, headers=admin).json()["matches"]
    assert len(pending) == 1 and pending[0]["recipe_id"] == data[0]
    assert data[3] in [row["item_id"] for row in pending[0]["candidates"]]
    before = client.get(f"/api/recipes/{data[0]}/availability", headers=admin).json()
    assert before["ingredients"][0]["status"] != "have"
    disabled = evaluate(client, admin, data)
    assert disabled["results"][0]["label"] == "insufficient_evidence" and not calls
    enabled(client, admin)
    original = truth()
    review = evaluate(client, admin, data)
    assert len(calls) == 1 and review["results"][0]["label"] == "same_ingredient"
    assert review["results"][0]["probability"] == pytest.approx(0.8)
    assert not review["results"][0]["clears_threshold"] and review["results"][0]["can_confirm"]
    assert truth() == original
    for _ in range(2):
        assert client.get(BASE + f"/reviews/{review['id']}", headers=admin).status_code == 200
        assert client.get(BASE, headers=admin).status_code == 200
        assert client.get(f"/api/recipes/{data[0]}/availability", headers=admin).json() == before
    assert len(calls) == 1


@pytest.mark.parametrize(
    "product,target,expected",
    [
        ("Cumin flavored chips", "Cumin", "possible_substitution"),
        ("Cumin seasoning blend", "Cumin", "possible_substitution"),
        ("No salt seasoning", "Salt", "different_ingredient"),
        ("Vanilla yogurt", "Plain yogurt", "possible_substitution"),
        ("Whole cumin", "Ground cumin", "possible_substitution"),
        ("Unknown cumin", "Cumin", "insufficient_evidence"),
    ],
)
def test_modifiers_unknown_and_recipe_permissiveness_never_learn_alias(
    client, admin, monkeypatch, product, target, expected
):
    enabled(client, admin)
    data = setup(
        client,
        admin,
        product=product,
        target=target,
        instructions="All flavors and all forms are acceptable in this dessert; use any substitute.",
    )
    calls = fixture(monkeypatch)
    original = truth()
    review = evaluate(client, admin, data)
    assert review["results"][0]["label"] == expected
    assert not review["results"][0]["can_confirm"] and not calls
    assert confirm(client, admin, review).status_code == 409
    assert truth() == original


def test_plausible_synonym_ranks_before_flavored_distractors(client, admin, monkeypatch):
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        location_id = str(db.scalar(select(Inventory.location_id)))
    for index in range(5):
        item = client.post(
            "/api/items", headers=admin, json={"name": f"Chickpeas flavored snack blend {index}"}
        ).json()["id"]
        client.post(
            "/api/inventory/add",
            headers=admin,
            json={"item_id": item, "location_id": location_id, "quantity": 1, "unit": "each"},
        )
    calls = fixture(monkeypatch)
    enabled(client, admin)
    review = evaluate(client, admin, data)
    assert len(review["results"]) == 3
    assert review["results"][0]["candidate_item_id"] == data[3]
    assert review["results"][0]["retrieval_reason"] == "culinary_synonym"
    assert len(calls) == 1


def test_selected_alias_only_no_reverse_no_recipe_stock_changes(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        location_id = str(db.scalar(select(Inventory.location_id)))
    other = client.post("/api/items", headers=admin, json={"name": "Organic garbanzo beans"}).json()["id"]
    client.post(
        "/api/inventory/add",
        headers=admin,
        json={"item_id": other, "location_id": location_id, "quantity": 2, "unit": "each"},
    )
    fixture(monkeypatch)
    original = truth()
    review = evaluate(client, admin, data)
    assert len(review["results"]) == 2 and review["ambiguous_tie"]
    response = confirm(client, admin, review, data[3])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"
    with SessionLocal() as db:
        target, selected, untouched = [db.get(Item, UUID(item_id)) for item_id in (data[2], data[3], other)]
        assert selected.canonical_ingredient == target.canonical_ingredient
        assert selected.canonical_provenance == "confirmed"
        assert untouched.canonical_ingredient != target.canonical_ingredient
    after = truth()
    assert {key: value for key, value in after.items() if key != "items"} == {
        key: value for key, value in original.items() if key != "items"
    }
    assert confirm(client, admin, review, other).status_code == 409


@pytest.mark.parametrize(
    "choice,expected",
    [("c_2", "possible_substitution"), ("c_3", "different_ingredient"), ("c_4", "insufficient_evidence")],
)
def test_model_nonidentity_cannot_confirm(client, admin, monkeypatch, choice, expected):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch, choice)
    original = truth()
    review = evaluate(client, admin, data)
    assert review["results"][0]["label"] == expected
    assert confirm(client, admin, review).status_code == 409 and truth() == original


def test_question_tie_abstains(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch, probability=0.25)
    review = evaluate(client, admin, data)
    assert review["ambiguous_tie"]
    assert review["results"][0]["label"] == "insufficient_evidence"
    assert confirm(client, admin, review).status_code == 409


@pytest.mark.parametrize("change", ["item", "recipe", "line", "stock", "profile", "config"])
def test_source_stock_context_configuration_changes_invalidate(client, admin, monkeypatch, change):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    if change == "profile":
        url = f"/api/items/{data[3]}/structured-evidence"
        current = client.get(url, headers=admin).json()
        assert (
            client.put(
                url,
                headers=admin,
                json={
                    "confirm": True,
                    "expected_revision": current["revision"],
                    "base_source_hash": current["base_source_hash"],
                    "facts": {"form": "whole"},
                },
            ).status_code
            == 200
        )
    elif change == "config":
        enabled(client, admin, review_confidence=0.91)
    else:
        with SessionLocal() as db:
            if change == "item":
                db.get(Item, UUID(data[3])).brand = "Changed"
            elif change == "recipe":
                db.get(Recipe, UUID(data[0])).instructions = "Changed context"
            elif change == "line":
                db.get(RecipeIngredient, UUID(data[1])).quantity_required = 3
            else:
                db.scalar(select(Inventory)).quantity = 3
            db.commit()
    current = client.get(BASE + f"/reviews/{review['id']}", headers=admin).json()
    assert current["stale"] and current["results"] == []
    assert confirm(client, admin, review).status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("confirm_global_alias", 1),
        ("confirm_global_alias", "true"),
        ("confirm_global_alias", False),
        ("replace_confirmed", 1),
        ("replace_confirmed", "true"),
    ],
)
def test_acknowledgements_are_strict(client, admin, monkeypatch, field, value):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert confirm(client, admin, review, **{field: value}).status_code == 422


def test_token_expiry_extra_fields_and_rejection_are_bound(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert "confirmation_token" not in client.get(BASE + f"/reviews/{review['id']}", headers=admin).json()
    wrong = {**review, "confirmation_token": "x" * 32}
    assert confirm(client, admin, wrong).status_code == 409
    assert confirm(client, admin, review, unexpected=True).status_code == 422
    original = truth()
    response = client.post(BASE + f"/reviews/{review['id']}/reject", headers=admin, json=binding(review))
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert truth() == original and confirm(client, admin, review).status_code == 409
    review = evaluate(client, admin, data)
    with SessionLocal() as db:
        db.get(RankedIngredientReview, UUID(review["id"])).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert confirm(client, admin, review).status_code == 409


def test_provider_failure_returns_manual_abstention_without_retry(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    calls = []

    async def failed(*args, **kwargs):
        calls.append(1)
        raise ClassifierFailure("unavailable", "timeout")

    monkeypatch.setattr(culinary_service, "evaluate_culinary", failed)
    original = truth()
    review = evaluate(client, admin, data)
    assert review["status"] == "interrupted" and review["provider_requests"] == 1
    assert review["results"][0]["label"] == "insufficient_evidence"
    assert calls == [1] and truth() == original


def test_same_item_bypasses_provider(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Cumin", target="Cumin")
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert review["results"][0]["decision_provenance"] == "same_item" and not calls


def test_confirmed_identity_with_auto_target_bypasses_provider(client, admin, monkeypatch):
    enabled(client, admin)
    calls = fixture(monkeypatch)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        target, product = db.get(Item, UUID(data[2])), db.get(Item, UUID(data[3]))
        product.canonical_ingredient = target.canonical_ingredient
        product.canonical_provenance = "confirmed"
        db.commit()
    review = evaluate(client, admin, data)
    assert review["results"][0]["decision_provenance"] == "confirmed_identity" and not calls


def test_human_auth_required_and_empty_discovery_is_explicit(client, admin, monkeypatch):
    data = setup(client, admin, product="Cumin", target="Chickpeas")
    calls = fixture(monkeypatch)
    assert client.post(BASE + "/evaluate", json={"recipe_id": data[0], "ingredient_id": data[1]}).status_code in (
        401,
        403,
    )
    assert (
        client.post(
            BASE + "/evaluate", headers=admin, json={"recipe_id": data[0], "ingredient_id": data[1], "extra": 1}
        ).status_code
        == 422
    )
    review = evaluate(client, admin, data)
    assert review["results"] == [] and "no_candidates" in review["reason_codes"] and not calls


def test_member_agent_and_other_admin_cannot_consume_review(client, admin, monkeypatch):
    from app.services.auth import create_user

    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert (
        client.post(
            BASE + "/evaluate",
            headers={**admin, "X-Agent-Key": "synthetic-agent"},
            json={"recipe_id": data[0], "ingredient_id": data[1]},
        ).status_code
        == 403
    )
    for role in ("member", "admin"):
        with SessionLocal() as db:
            create_user(db, email=f"other-{role}@example.com", password="OtherPass987!", role=role)
        login = client.post("/api/auth/login", json={"email": f"other-{role}@example.com", "password": "OtherPass987!"})
        other = {"Authorization": "Bearer " + login.json()["token"]}
        assert confirm(client, other, review).status_code == (403 if role == "member" else 404)


@pytest.mark.parametrize("length", [1500, 2100])
def test_identity_tail_truncation_fails_closed_even_for_same_fixture(client, admin, monkeypatch, length):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        db.get(Item, UUID(data[3])).ingredients_text = (
            "Garbanzo beans, " + "ordinary text " * length + ", vanilla flavor blend"
        )
        db.commit()
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert review["results"][0]["label"] == "insufficient_evidence"
    assert "identity_evidence_truncated" in review["results"][0]["reason_codes"]
    assert not calls and review["provider_requests"] == 0
    assert confirm(client, admin, review).status_code == 409


def test_question_contains_both_compositions_and_provenance_but_no_capabilities(client, admin, monkeypatch):
    import json

    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        db.get(Item, UUID(data[2])).ingredients_text = "Chickpeas and water"
        db.get(Item, UUID(data[3])).ingredients_text = "Garbanzo beans and water"
        db.commit()
    url = f"/api/items/{data[3]}/structured-evidence"
    current = client.get(url, headers=admin).json()
    assert (
        client.put(
            url,
            headers=admin,
            json={
                "confirm": True,
                "expected_revision": current["revision"],
                "base_source_hash": current["base_source_hash"],
                "facts": {"identity": "Garbanzo beans", "form": "whole", "preparation": "canned"},
            },
        ).status_code
        == 200
    )
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert len(calls) == 1
    assert review["requested_evidence"]["ingredients_text"] == "Chickpeas and water"
    assert review["results"][0]["product_evidence"]["current_facts"]["preparation"] == "canned"
    assert review["results"][0]["product_evidence"]["facts_provenance"] == "saved_user_provided"
    state = json.loads(calls[0].state)
    assert state["evidence"]["requested"]["ingredients_text"] == "Chickpeas and water"
    assert state["evidence"]["product"]["saved_facts"]["preparation"] == "canned"
    assert state["evidence"]["product"]["saved_fact_provenance"] == "saved_user_provided"
    assert all(
        value not in calls[0].state
        for value in (*data, review["confirmation_token"], review["evidence_hash"], "recipe_instructions")
    )


def test_concurrent_first_confirmation_consumes_only_once(client, admin, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    barrier = Barrier(2)

    def save():
        barrier.wait()
        return confirm(client, admin, review).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: save(), range(2)))
    assert statuses == [200, 409]
    with SessionLocal() as db:
        assert db.get(Item, UUID(data[3])).canonical_revision == 1


def test_phantom_stock_and_new_retrieval_candidate_invalidate(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    fixture(monkeypatch)
    review = evaluate(client, admin, data)
    loc = client.post("/api/locations", headers=admin, json={"name": "Second shelf"}).json()["id"]
    other = client.post("/api/items", headers=admin, json={"name": "Chickpeas"}).json()["id"]
    client.post(
        "/api/inventory/add", headers=admin, json={"item_id": other, "location_id": loc, "quantity": 1, "unit": "each"}
    )
    assert confirm(client, admin, review).status_code == 409


def test_migration_add_remove_preserves_existing_items(client, admin, monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    from app.db.session import engine

    item_id = client.post("/api/items", headers=admin, json={"name": "Migration survivor"}).json()["id"]
    path = Path(__file__).parents[1] / "migrations/versions/0063_ranked_ingredient_reviews.py"
    spec = importlib.util.spec_from_file_location("ranked_review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        RankedIngredientReview.__table__.drop(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert "ranked_ingredient_reviews" in inspect(connection).get_table_names()
        migration.downgrade()
        assert "ranked_ingredient_reviews" not in inspect(connection).get_table_names()
        assert (
            connection.execute(select(Item.name).where(Item.id == UUID(item_id))).scalar_one() == "Migration survivor"
        )


def test_stale_saved_facts_are_ignored_without_vetoing_current_catalog_evidence(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    url = f"/api/items/{data[3]}/structured-evidence"
    current = client.get(url, headers=admin).json()
    assert (
        client.put(
            url,
            headers=admin,
            json={
                "confirm": True,
                "expected_revision": current["revision"],
                "base_source_hash": current["base_source_hash"],
                "facts": {"identity": "Garbanzo beans", "preparation": "canned"},
            },
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        db.get(Item, UUID(data[3])).brand = "Changed source"
        db.commit()
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    result = review["results"][0]
    assert result["label"] == "same_ingredient"
    assert result["product_evidence"]["saved_facts_stale"]
    assert not any(result["product_evidence"]["current_facts"].values())
    assert len(calls) == 1 and result["can_confirm"]
    assert "canned" not in calls[0].state


def test_saved_composition_synonym_wording_is_unresolved_not_automatic_conflict(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    for item_id, composition in (
        (data[2], "chickpeas, water, salt"),
        (data[3], "garbanzo beans (chickpeas), water, salt"),
    ):
        url = f"/api/items/{item_id}/structured-evidence"
        current = client.get(url, headers=admin).json()
        assert (
            client.put(
                url,
                headers=admin,
                json={
                    "confirm": True,
                    "expected_revision": current["revision"],
                    "base_source_hash": current["base_source_hash"],
                    "facts": {"form": "whole", "preparation": "canned", "composition": composition},
                },
            ).status_code
            == 200
        )
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert len(calls) == 1
    result = review["results"][0]
    assert result["label"] == "same_ingredient" and result["decision_provenance"] == "classifier"
    assert result["can_confirm"]


def test_old_confirmed_alias_precedes_more_than_fifty_newer_distractors(client, admin, monkeypatch):
    enabled(client, admin)
    data = setup(client, admin, product="Garbanzo beans", target="Chickpeas")
    with SessionLocal() as db:
        target, product = db.get(Item, UUID(data[2])), db.get(Item, UUID(data[3]))
        product.canonical_ingredient = target.canonical_ingredient
        product.canonical_provenance = "confirmed"
        product.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        location = str(db.scalar(select(Inventory.location_id)))
        db.commit()
    for index in range(51):
        item = client.post(
            "/api/items", headers=admin, json={"name": f"Chickpeas flavored snack blend {index}"}
        ).json()["id"]
        assert client.post(
            "/api/inventory/add",
            headers=admin,
            json={"item_id": item, "location_id": location, "quantity": 1, "unit": "each"},
        ).status_code in (200, 201)
    calls = fixture(monkeypatch)
    review = evaluate(client, admin, data)
    assert review["results"][0]["candidate_item_id"] == data[3]
    assert review["results"][0]["decision_provenance"] == "confirmed_identity"
    assert not calls and review["provider_requests"] == 0
