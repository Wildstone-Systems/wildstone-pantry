"""Isolated real API/SQLite review workflow; model answers are fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Item, Recipe, RecipeIngredient
from modules.automation import culinary_service
from modules.automation.providers.classifier_common import ClassifierFailure
from modules.planning.models.ingredient_match_review import IngredientMatchReview

BASE = "/api/ingredient-match-reviews"


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "review@example.com", "password": "ReviewPass987!"})
    r = client.post("/api/auth/login", json={"email": "review@example.com", "password": "ReviewPass987!"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def setup(
    client,
    admin,
    product="Smoked chipotle spice",
    target="Chipotle powder",
    instructions="Season the sauce; reducing added salt is allowed.",
):
    def item(name):
        r = client.post("/api/items", headers=admin, json={"name": name})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    wanted, stocked = item(target), item(product)
    if product == target:
        stocked = wanted
    loc = client.post("/api/locations", headers=admin, json={"name": "Pantry"}).json()["id"]
    r = client.post(
        "/api/inventory/add",
        headers=admin,
        json={"item_id": stocked, "location_id": loc, "quantity": 2, "unit": "each"},
    )
    assert r.status_code in (200, 201), r.text
    recipe = client.post("/api/recipes", headers=admin, json={"name": "Sauce", "instructions": instructions}).json()[
        "id"
    ]
    r = client.put(
        f"/api/recipes/{recipe}/ingredients",
        headers=admin,
        json=[{"item_id": wanted, "quantity_required": 1, "unit": "each"}],
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        ingredient = str(db.scalar(select(RecipeIngredient.id).where(RecipeIngredient.recipe_id == UUID(recipe))))
    return recipe, ingredient, wanted, stocked


def create(client, admin, data, facts=None):
    r = client.post(
        f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-reviews",
        headers=admin,
        json={
            "candidate_item_id": data[3],
            "facts": facts if facts is not None else {"form": "powder", "preparation": "dried"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def binding(view, token=None):
    return {
        "revision": view["revision"],
        "evidence_hash": view["evidence_hash"],
        "confirmation_token": token or view["confirmation_token"],
    }


def confirm(client, admin, view, token=None):
    return client.post(BASE + f"/{view['id']}/confirm", headers=admin, json={**binding(view, token), "confirm": True})


def enabled(client, admin, **extra):
    r = client.put(
        "/api/ai/classifier/config",
        headers=admin,
        json={
            "enabled": True,
            "runtime_review_enabled": True,
            "execution_location": "local",
            "endpoint": "http://localhost:11434",
            "model": "clef",
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def answer(request, choice="c_2", probability=0.7):
    return {
        key: {
            "type": "choice",
            "choice": choice,
            "confidence": 0.99,
            "probabilities": {
                label: probability if label == choice else (1 - probability) / (len(q.criteria) - 1)
                for label in q.criteria
            },
        }
        for key, q in request.questions.items()
    }


def test_manual_scope_reload_no_truth_writes(client, admin):
    data = setup(client, admin)
    with SessionLocal() as db:
        item = db.get(Item, UUID(data[3]))
        original = (item.canonical_ingredient, item.canonical_revision)
    review = create(client, admin, data)
    assert review["can_confirm"] and not review["can_compare"]
    assert review["packet"]["product"]["nutrition_values"]["energy_kcal_100g"] is None
    result = confirm(client, admin, review)
    assert result.status_code == 200, result.text
    assert result.json()["current_choice"] and "confirmation_token" not in result.json()
    with SessionLocal() as db:
        item = db.get(Item, UUID(data[3]))
        ingredient = db.get(RecipeIngredient, UUID(data[1]))
        assert (item.canonical_ingredient, item.canonical_revision) == original
        assert str(ingredient.item_id) == data[2] and ingredient.quantity_required == 1
        row = db.get(IngredientMatchReview, UUID(review["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert client.get(BASE + f"/{review['id']}", headers=admin).json()["current_choice"]


@pytest.mark.parametrize("name", ["CHK BRST", "Unlabeled powder"])
@pytest.mark.parametrize("fact", ["unknown", "?", "  "])
def test_unknown_stops_all_io_and_confirmation(client, admin, monkeypatch, name, fact):
    enabled(client, admin)
    data = setup(client, admin, product=name)
    review = create(client, admin, data, {"identity": fact, "form": "powder", "preparation": "dried"})
    assert review["readiness"] == "needs_information" and not review["can_confirm"]

    async def forbidden(*a, **kw):
        pytest.fail("Unknown dispatched")

    monkeypatch.setattr(culinary_service, "evaluate_culinary", forbidden)
    r = client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review))
    assert r.status_code == 200 and r.json()["provider_requests"] == 0
    assert confirm(client, admin, review).status_code == 409


def test_exact_known_identity_bypasses_unnecessary_prep_questions(client, admin):
    enabled(client, admin)
    review = create(client, admin, setup(client, admin, product="Paprika", target="Paprika"), {})
    assert review["missing_fields"] == [] and review["can_confirm"] and not review["can_compare"]
    assert (
        client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review)).json()["provider_requests"]
        == 0
    )


@pytest.mark.parametrize("last_choice,expected", [("c_2", "possible_match"), ("c_3", "not_recommended")])
def test_two_stage_low_confidence_and_manual_override(client, admin, monkeypatch, last_choice, expected):
    enabled(client, admin)
    data = setup(client, admin)
    calls = []

    async def evaluate(db, request, **kw):
        calls.append(request)
        assert not await kw["stop_requested"]()
        return answer(request, "c_2" if len(calls) == 1 else last_choice)

    monkeypatch.setattr(culinary_service, "evaluate_culinary", evaluate)
    review = create(client, admin, data)
    r = client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review))
    assert r.status_code == 200, r.text
    view = r.json()
    assert len(calls) == view["provider_requests"] == 2
    assert "reducing added salt" not in calls[0].state and "reducing added salt" in calls[1].state
    assert view["readiness"] == expected and view["can_confirm"] and not view["can_compare"]
    assert not any(p["clears_threshold"] for p in view["predictions"])
    if last_choice == "c_3":
        assert all(p["purpose"] != "adjustment" for p in view["predictions"])
    assert (
        client.post(
            BASE + f"/{review['id']}/compare", headers=admin, json=binding(view, review["confirmation_token"])
        ).status_code
        == 200
    )
    assert len(calls) == 2
    result = confirm(client, admin, view, review["confirmation_token"])
    assert result.status_code == 200, result.text
    if last_choice == "c_3":
        assert "manual_choice_despite_known_conflict" in result.json()["reason_codes"]


def test_model_unknown_identity_stops_downstream(client, admin, monkeypatch):
    enabled(client, admin)
    calls = []

    async def evaluate(db, request, **kw):
        calls.append(request)
        return answer(request, "c_4", 0.97)

    monkeypatch.setattr(culinary_service, "evaluate_culinary", evaluate)
    review = create(client, admin, setup(client, admin))
    view = client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review)).json()
    assert len(calls) == 1 and view["readiness"] == "needs_information" and not view["can_confirm"]


def test_scope_cas_old_pending_cannot_replace_new_choice(client, admin):
    data = setup(client, admin)
    first = create(client, admin, data)
    second = create(client, admin, data)
    assert confirm(client, admin, first).status_code == 200
    assert confirm(client, admin, second).status_code == 409
    fresh = create(client, admin, data)
    assert confirm(client, admin, fresh).status_code == 200
    assert not client.get(BASE + f"/{first['id']}", headers=admin).json()["current_choice"]


@pytest.mark.parametrize("field", ["ingredients", "instructions", "policy"])
def test_full_source_and_config_stale_tokens(client, admin, field):
    data = setup(client, admin)
    with SessionLocal() as db:
        obj = db.get(Item, UUID(data[3])) if field == "ingredients" else db.get(Recipe, UUID(data[0]))
        if field == "ingredients":
            obj.ingredients_text = "x" * 2200
        if field == "instructions":
            obj.instructions = "x" * 2200
        db.commit()
    review = create(client, admin, data)
    if field == "policy":
        assert (
            client.put("/api/ai/classifier/config", headers=admin, json={"review_confidence": 0.97}).status_code == 200
        )
    else:
        with SessionLocal() as db:
            obj = db.get(Item, UUID(data[3])) if field == "ingredients" else db.get(Recipe, UUID(data[0]))
            if field == "ingredients":
                obj.ingredients_text += " beyond clip"
            else:
                obj.instructions += " beyond clip"
            db.commit()
    assert confirm(client, admin, review).status_code == 409
    assert client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review)).status_code == 409
    assert confirm(client, admin, create(client, admin, data)).status_code == 200


def test_provider_outage_keeps_explicit_manual_path(client, admin, monkeypatch):
    enabled(client, admin)

    async def evaluate(*a, **kw):
        raise ClassifierFailure("unavailable", "transport")

    monkeypatch.setattr(culinary_service, "evaluate_culinary", evaluate)
    review = create(client, admin, setup(client, admin))
    view = client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review)).json()
    assert view["status"] == "interrupted" and view["can_confirm"] and view["provider_requests"] == 1
    assert confirm(client, admin, view, review["confirmation_token"]).status_code == 200


@pytest.mark.parametrize("value", [False, 1, "true"])
def test_confirmation_strict_true(client, admin, value):
    view = create(client, admin, setup(client, admin))
    assert (
        client.post(
            BASE + f"/{view['id']}/confirm", headers=admin, json={**binding(view), "confirm": value}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", [-0.1, 1.1, True, None, "0.9"])
def test_threshold_strict_finite(client, admin, value):
    assert client.put("/api/ai/classifier/config", headers=admin, json={"review_confidence": value}).status_code == 400


def test_default90_custom97_preserved(client, admin):
    default = client.get("/api/ai/classifier/config", headers=admin).json()["config"]
    assert default["review_confidence"] == 0.90 and default["runtime_review_enabled"] is False
    assert enabled(client, admin, review_confidence=0.97)["config"]["review_confidence"] == 0.97
    r = client.put("/api/ai/classifier/config", headers=admin, json={"enabled": False})
    assert r.json()["config"]["review_confidence"] == 0.97


@pytest.mark.parametrize(
    "context,product,target,conflict",
    [
        ("For a savory sauce, sweetness is explicitly unwanted.", "Vanilla yogurt", "Plain yogurt", True),
        ("For a sweet sauce, sweetness is not unwanted.", "Vanilla yogurt", "Plain yogurt", False),
        ("For a savory sauce, sugar is unwanted.", "Sugar-free yogurt", "Plain yogurt", False),
        ("Use a gluten-free adaptation.", "Almond flour", "Wheat flour", False),
        ("The dough uses kneading to develop the wheat gluten network.", "Almond flour", "Wheat flour", True),
    ],
)
def test_explicit_constraint_warnings_are_negation_aware(client, admin, context, product, target, conflict):
    enabled(client, admin)
    view = create(client, admin, setup(client, admin, product=product, target=target, instructions=context))
    assert (
        view["readiness"] == "manual_review"
    )  # source context is judged by the model/person, not benchmark phrase rules
    assert view["can_compare"]  # known differences may still be compared; never an automatic ban


def test_expired_evaluating_is_recovered(client, admin):
    data = setup(client, admin)
    view = create(client, admin, data)
    with SessionLocal() as db:
        row = db.get(IngredientMatchReview, UUID(view["id"]))
        row.status = "evaluating"
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert create(client, admin, data)["id"] != view["id"]
    assert client.get(BASE + f"/{view['id']}", headers=admin).status_code == 404


def test_permission_real_admin_only(client, admin):
    data = setup(client, admin)
    path = f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-reviews"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={**admin, "X-Agent-Key": "synthetic-agent"}).status_code == 403
    from app.services.auth import create_user

    with SessionLocal() as db:
        create_user(db, email="member@example.com", password="MemberPass987!", role="member")
    r = client.post("/api/auth/login", json={"email": "member@example.com", "password": "MemberPass987!"})
    assert client.get(path, headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 403


def test_adapter_stream_cancel_closes_transport_without_context_or_writes(client, admin, monkeypatch):
    import anyio
    import httpx

    from modules.automation.providers import clef
    from modules.planning.schemas.ingredient_match_review import ReviewBinding

    enabled(client, admin)
    review = create(client, admin, setup(client, admin))
    calls = []
    closed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await anyio.sleep(0.01)

        async def aclose(self):
            closed.append(True)

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Body())

    monkeypatch.setattr(clef, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with SessionLocal() as db:
        actor = db.get(IngredientMatchReview, UUID(review["id"])).actor_id

        async def disconnected():
            return bool(calls)

        async def execute():
            return await service.compare(
                db, UUID(review["id"]), ReviewBinding.model_validate(binding(review)), actor, disconnected
            )

        import modules.planning.services.ingredient_match_reviews as service

        result = anyio.run(execute)
    assert len(calls) == len(closed) == 1
    assert result["provider_requests"] == 1 and result["status"] == "interrupted"
    assert result["predictions"] == [] and result["can_confirm"]


def test_real_adapter_receives_closed_two_stage_packets(client, admin, monkeypatch):
    import json

    import httpx

    from modules.automation.providers import clef

    enabled(client, admin)
    data = setup(client, admin)
    calls = []
    with SessionLocal() as db:
        item = db.get(Item, UUID(data[3]))
        item.sodium_g_100g = 20
        item.serving_size_g = 5
        item.nutrition_estimated = True
        db.commit()

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        answers = {
            key: {
                "type": "choice",
                "choice": "c_2",
                "confidence": 0.75,
                "probabilities": {
                    label: 0.75 if label == "c_2" else 0.25 / (len(q["criteria"]) - 1) for label in q["criteria"]
                },
            }
            for key, q in payload["questions"].items()
        }
        return httpx.Response(200, json={"model": "clef", "answers": answers})

    monkeypatch.setattr(clef, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    review = create(client, admin, data)
    r = client.post(BASE + f"/{review['id']}/compare", headers=admin, json=binding(review))
    assert r.status_code == 200, r.text
    assert len(calls) == 2 and len(r.json()["predictions"]) == 3
    state = json.loads(calls[1]["state"])["evidence"]
    assert state["recipe_instructions"] == "Season the sauce; reducing added salt is allowed."
    assert state["product"]["nutrition_estimated"] is True
    assert float(state["product"]["nutrition_values"]["sodium_g_100g"]) == 20
    assert "quantity" not in state and "source_fingerprint" not in calls[1]["state"]


def test_config_change_after_scope_lock_before_binding_is_rejected(client, admin, monkeypatch):
    from modules.shopping.services import connector_config

    data = setup(client, admin)
    view = create(client, admin, data)
    # A real second session commits the change before the confirmation obtains
    # the configuration-owner lock. The fresh locked policy must reject it.
    original_policy = culinary_service.review_policy
    changed = []

    def race(db, *, lock=False):
        if lock and not changed:
            with SessionLocal() as other:
                connector_config.set_classifier_config(other, {"review_confidence": 0.97})
            changed.append(True)
        return original_policy(db, lock=lock)

    monkeypatch.setattr(culinary_service, "review_policy", race)
    assert confirm(client, admin, view).status_code == 409
    with SessionLocal() as db:
        row = db.get(IngredientMatchReview, UUID(view["id"]))
        assert row.status == "pending" and row.document["selected_source_item_id"] is None


def test_two_session_scope_cas_cannot_overwrite_newer_choice(client, admin):
    import copy

    from modules.planning import ingredient_match_repository as repo
    from modules.planning.models.ingredient_match_review import IngredientMatchScope

    data = setup(client, admin)
    first = create(client, admin, data)
    second = create(client, admin, data)
    with SessionLocal() as stale:
        old = repo.get(stale, UUID(second["id"]))
        doc = copy.deepcopy(old.document)
        assert confirm(client, admin, first).status_code == 200
        with pytest.raises(ValueError, match="superseded"):
            repo.confirm(stale, old, doc, expected_revision=old.revision)
    with SessionLocal() as db:
        assert str(db.get(IngredientMatchScope, UUID(data[1])).current_review_id) == first["id"]


def test_wrong_recipe_relation_and_actor_binding(client, admin):
    from app.services.auth import create_user

    data = setup(client, admin)
    review = create(client, admin, data)
    other_recipe = client.post("/api/recipes", headers=admin, json={"name": "Other"}).json()["id"]
    assert (
        client.get(f"/api/recipes/{other_recipe}/ingredients/{data[1]}/match-candidates", headers=admin).status_code
        == 404
    )
    with SessionLocal() as db:
        create_user(db, email="second@example.com", password="SecondPass987!", role="admin")
    token = client.post("/api/auth/login", json={"email": "second@example.com", "password": "SecondPass987!"}).json()[
        "token"
    ]
    assert confirm(client, {"Authorization": "Bearer " + token}, review).status_code == 404


def test_review_migration_roundtrip_preserves_existing_food():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = importlib.import_module("migrations.versions.0061_ingredient_match_reviews")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_food(name TEXT)"))
        connection.execute(text("INSERT INTO existing_food VALUES ('Apple')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "ingredient_match_scopes" in inspect(connection).get_table_names()
            migration.downgrade()
            assert "ingredient_match_reviews" not in inspect(connection).get_table_names()
            migration.upgrade()
        assert connection.scalar(text("SELECT name FROM existing_food")) == "Apple"


def test_confirmed_base_identity_does_not_collapse_whole_ground_forms(client, admin):
    enabled(client, admin)
    data = setup(client, admin, product="Raw whole cumin", target="Raw ground cumin")
    with SessionLocal() as db:
        for key in data[2:]:
            item = db.get(Item, UUID(key))
            item.canonical_provenance = "confirmed"
            item.canonical_ingredient = "cumin"
            item.ingredients_text = "cumin"
            item.canonical_revision = 2
        db.commit()
    view = create(client, admin, data, {})
    assert view["can_compare"] and view["readiness"] == "manual_review"


def test_same_name_different_composition_requires_review(client, admin):
    enabled(client, admin)
    data = setup(client, admin, product="Raw ground cumin blend", target="Raw ground cumin")
    with SessionLocal() as db:
        product = db.get(Item, UUID(data[3]))
        product.name = "Raw ground cumin"
        product.ingredients_text = "cumin, salt"
        target = db.get(Item, UUID(data[2]))
        target.ingredients_text = "cumin"
        db.commit()
    view = create(client, admin, data, {})
    assert view["can_compare"]


@pytest.mark.parametrize(
    "target,product",
    [
        ("Single-ingredient chipotle powder", "Smoked chipotle seasoning"),
        ("Plain unsweetened Greek yogurt", "Vanilla yogurt"),
    ],
)
def test_default_candidates_search_ingredient_terms_not_modifiers(client, admin, target, product):
    data = setup(client, admin, product=product, target=target)
    response = client.get(f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-candidates", headers=admin)
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert data[3] in [candidate["item"]["item_id"] for candidate in candidates]
    assert len(candidates) <= 5


@pytest.mark.parametrize("another_admin", [False, True])
def test_history_pins_current_choice_before_newer_actor_reviews(client, admin, another_admin):
    from app.services.auth import create_user

    data = setup(client, admin)
    older = create(client, admin, data)
    viewer = admin
    if another_admin:
        with SessionLocal() as db:
            create_user(db, email="history@example.com", password="HistoryPass987!", role="admin")
        login = client.post("/api/auth/login", json={"email": "history@example.com", "password": "HistoryPass987!"})
        viewer = {"Authorization": "Bearer " + login.json()["token"]}
    newer_ids = []
    for index in range(22):
        pending = create(client, viewer, data)
        newer_ids.append(pending["id"])
        if index < 2:
            rejected = client.post(BASE + f"/{pending['id']}/reject", headers=viewer, json=binding(pending))
            assert rejected.status_code == 200, rejected.text
    # Confirm an older pending record after >20 later pending/rejected records.
    assert confirm(client, admin, older).status_code == 200
    response = client.get(f"/api/recipes/{data[0]}/ingredients/{data[1]}/match-reviews", headers=viewer)
    assert response.status_code == 200, response.text
    rows = response.json()["reviews"]
    assert len(rows) == 20
    assert rows[0]["id"] == older["id"] and rows[0]["current_choice"]
    assert rows[0]["status"] == "confirmed"
    assert [row["id"] for row in rows[1:]] == list(reversed(newer_ids))[:19]
    assert len({row["id"] for row in rows}) == 20
