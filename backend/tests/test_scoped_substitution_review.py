"""Scoped human choices, not model verdicts, authorize directional cook swaps."""

from decimal import Decimal
from uuid import UUID

import pytest


def setup(client, *, quantity_mode="amount", source_name="Chipotle powder", target_name="Smoked chipotle spice"):
    credentials = {"email": "scoped-review@example.test", "password": "SyntheticScope987!"}
    assert client.post("/api/auth/bootstrap-admin", json=credentials).status_code == 200
    token = client.post("/api/auth/login", json=credentials).json()["token"]
    source = client.post("/api/items", json={"name": source_name, "stock_mode": "measured", "base_unit": "g"}).json()[
        "id"
    ]
    target = client.post("/api/items", json={"name": target_name, "stock_mode": "measured", "base_unit": "g"}).json()[
        "id"
    ]
    location = client.post("/api/locations", json={"name": "Synthetic scope pantry"}).json()["id"]
    assert (
        client.post(
            "/api/inventory/add", json={"item_id": target, "location_id": location, "quantity": 50, "unit": "g"}
        ).status_code
        == 200
    )
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Cold chipotle sauce",
            "instructions": "Stir sauce. Serve cold.",
            "servings": 1,
            "ingredients": [{"item_id": source, "quantity": 10, "unit": "g", "quantity_mode": quantity_mode}],
        },
    ).json()["id"]
    ingredient = client.get(f"/api/recipes/{recipe}").json()["ingredients"][0]["ingredient_id"]
    session = client.post("/api/cook-sessions", json={"recipe_id": recipe}).json()["id"]
    return {
        "source": source,
        "target": target,
        "location": location,
        "recipe": recipe,
        "ingredient": ingredient,
        "session": session,
        "headers": {"Authorization": "Bearer " + token},
    }


def review(client, data, **changes):
    payload = {
        "ingredient_id": data["ingredient"],
        "substitute_item_id": data["target"],
        "context": {
            "cooking_role": "seasoning",
            "preparation": "cold sauce",
            "restrictions": "No stated restrictions; label checked manually",
        },
        **changes,
    }
    response = client.post(f"/api/cook-sessions/{data['session']}/substitution-reviews", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, row, **changes):
    return client.post(
        f"/api/substitution-reviews/{row['id']}/approve",
        json={"expected_binding": row["binding"], "confirmed": True, "ratio": 1.25, "unit": "g", **changes},
    )


def counts():
    from app.db import SessionLocal
    from app.models import IngredientSubstitution, InventoryTransaction, Item, Recipe, RecipeCookEvent

    with SessionLocal() as db:
        return tuple(
            db.query(model).count()
            for model in (Item, Recipe, IngredientSubstitution, InventoryTransaction, RecipeCookEvent)
        )


def test_one_use_is_manual_no_global_and_finish_consumes_once_with_historical_retry(client):
    data = setup(client)
    before = counts()
    row = review(client, data)
    assert counts() == before
    assert row["scope"] is None and row["evaluation"] is None and not row["stale"]
    result = approve(client, row)
    assert result.status_code == 200, result.text
    assert result.json()["scope"] == "one_use"
    assert counts() == before
    assert approve(client, row).status_code == 200
    session = client.get(f"/api/cook-sessions/{data['session']}").json()
    assert session["ingredients"][0]["item"] == "Smoked chipotle spice"
    assert session["ingredients"][0]["quantity"] == "12.5"
    assert session["plan"]["substitution_approval_ids"] == [row["id"]]
    client.post(f"/api/cook-sessions/{data['session']}/step", json={"action": "next"})
    finish = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert finish.status_code == 200, finish.text
    assert finish.json()["session"]["scoped_substitutions"][0]["scope"] == "one_use"
    after = counts()
    assert after[2] == before[2] and after[3] == before[3] + 1 and after[4] == before[4] + 1
    client.put(f"/api/items/{data['target']}", json={"name": "Renamed after cooking"})
    client.put(f"/api/recipes/{data['recipe']}", json={"name": "Changed after cooking"})
    retried = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["cook"]["already_finished"]
    assert retried.json()["session"]["ingredients"][0]["item"] == "Smoked chipotle spice"
    assert counts() == after
    rows = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"]
    assert rows[0]["status"] == "consumed" and rows[0]["consumed_at"]


@pytest.mark.parametrize("change", ["recipe", "catalog", "servings"])
def test_stale_context_blocks_confirmation_and_writes(client, change):
    data = setup(client)
    row = review(client, data)
    if change == "recipe":
        client.put(f"/api/recipes/{data['recipe']}", json={"name": "Different recipe context"})
    elif change == "catalog":
        client.put(f"/api/items/{data['target']}", json={"name": "Sweetened chipotle spice"})
    else:
        client.patch(f"/api/cook-sessions/{data['session']}", json={"servings_made": 2})
    before = counts()
    result = approve(client, row)
    assert result.status_code == 409, result.text
    assert counts() == before


def test_plan_change_after_approval_blocks_finish_but_progress_does_not(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    client.patch(f"/api/cook-sessions/{data['session']}", json={"servings_made": 2})
    before = counts()
    result = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert result.status_code == 400, result.text
    assert counts() == before
    assert client.get(f"/api/cook-sessions/{data['session']}").json()["status"] == "active"


def test_recipe_approval_needs_explicit_reuse_and_never_leaks_to_other_recipe(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row, scope="recipe").status_code == 200
    other = client.post(
        "/api/recipes",
        json={"name": "Other", "ingredients": [{"item_id": data["source"], "quantity": 10, "unit": "g"}]},
    ).json()["id"]
    other_session = client.post("/api/cook-sessions", json={"recipe_id": other}).json()["id"]
    rejected = client.post(
        f"/api/cook-sessions/{other_session}/substitution-reviews/{row['id']}/attach",
        json={"confirmed": True, "expected_binding": row["binding"]},
    )
    assert rejected.status_code == 409
    next_session = client.post("/api/cook-sessions", json={"recipe_id": data["recipe"]}).json()["id"]
    assert client.get(f"/api/cook-sessions/{next_session}").json()["scoped_substitutions"] == []
    saved = client.get(f"/api/cook-sessions/{next_session}/substitution-reviews").json()["recipe_approvals"][0]
    attached = client.post(
        f"/api/cook-sessions/{next_session}/substitution-reviews/{row['id']}/attach",
        json={"confirmed": True, "expected_binding": saved["binding"]},
    )
    assert attached.status_code == 200, attached.text
    assert client.get(f"/api/cook-sessions/{next_session}").json()["ingredients"][0]["quantity"] == "12.5"
    assert client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"] == []


def test_global_requires_permanent_and_existing_replacement_ack_preserves_legacy_until_confirmed(client):
    data = setup(client)
    legacy = client.post(
        "/api/substitutions",
        headers=data["headers"],
        json={"from_item_id": data["source"], "to_item_id": data["target"], "ratio": 2, "unit": "g"},
    )
    assert legacy.status_code == 201, legacy.text
    row = review(client, data)
    assert row["existing_global"]["from_unit"] is None
    assert approve(client, row, scope="global").status_code == 409
    assert approve(client, row, scope="global", permanent_ack=True).status_code == 409
    current = client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"][0]
    assert current["from_unit"] is None and Decimal(current["ratio"]) == 2
    approved = approve(client, row, scope="global", permanent_ack=True, replace_existing_ack=True)
    assert approved.status_code == 200, approved.text
    current = client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"][0]
    assert current["from_unit"] == "g" and Decimal(current["ratio"]) == Decimal("1.25")
    assert client.get(f"/api/substitutions/for/{data['target']}", headers=data["headers"]).json()["substitutions"] == []


def test_new_global_basis_is_skipped_for_other_recipe_units_and_rejected_by_legacy_cook(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row, scope="global", permanent_ack=True).status_code == 200
    other = client.post(
        "/api/recipes",
        json={"name": "Unit changed", "ingredients": [{"item_id": data["source"], "quantity": 1, "unit": "cup"}]},
    ).json()["id"]
    availability = client.get(f"/api/recipes/{other}/availability").json()
    assert availability["ingredients"][0]["status"] != "substitute"
    before = counts()
    cooked = client.post(
        f"/api/recipes/{other}/cook",
        json={"substitutions": [{"original_item_id": data["source"], "substitute_item_id": data["target"]}]},
    )
    assert cooked.status_code == 400, cooked.text
    assert counts() == before


def test_package_rows_and_forged_plan_approval_are_rejected_without_replacing_session(client):
    data = setup(client, quantity_mode="package")
    bad = client.post(
        f"/api/cook-sessions/{data['session']}/substitution-reviews",
        json={"ingredient_id": data["ingredient"], "substitute_item_id": data["target"]},
    )
    assert bad.status_code == 409 and "Package" in bad.text
    forged = client.post(
        "/api/cook-sessions",
        json={"recipe_id": data["recipe"], "plan": {"substitution_approval_ids": [data["source"]]}},
    )
    assert forged.status_code == 400
    assert client.get("/api/cook-sessions/active").json()["id"] == data["session"]


def test_missing_ratio_confirmation_and_agent_writes_never_approve(client):
    data = setup(client)
    row = review(client, data)
    assert (
        client.post(
            f"/api/substitution-reviews/{row['id']}/approve",
            json={"expected_binding": row["binding"], "confirmed": True, "unit": "g"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/substitution-reviews/{row['id']}/approve",
            json={"expected_binding": row["binding"], "ratio": 1, "unit": "g", "confirmed": False},
        ).status_code
        == 422
    )
    assert client.post(f"/api/substitution-reviews/{row['id']}/evaluate", json={}).status_code == 401
    assert client.post(
        f"/api/substitution-reviews/{row['id']}/approve",
        headers={"X-Agent-Key": "synthetic-invalid"},
        json={"expected_binding": row["binding"], "ratio": 1, "unit": "g", "confirmed": True},
    ).status_code in (401, 403)


def test_omitted_new_plan_key_preserves_attached_choice_and_revoke_blocks_it(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    assert (
        client.patch(
            f"/api/cook-sessions/{data['session']}", json={"plan": {"ingredient_overrides": [], "substitutions": []}}
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/cook-sessions/{data['session']}").json()["scoped_substitutions"][0]["approval_id"]
        == row["id"]
    )
    assert client.delete(f"/api/substitution-reviews/{row['id']}").status_code == 204
    before = counts()
    assert client.post(f"/api/cook-sessions/{data['session']}/finish", json={}).status_code == 400
    assert counts() == before


def configure(**changes):
    from app.db import SessionLocal
    from modules.automation.classifier_service import update_config

    with SessionLocal() as db:
        update_config(
            db,
            {
                "enabled": True,
                "cloud_opt_in": True,
                "runtime_review_enabled": True,
                "api_key": "synthetic-fixture-key",
                **changes,
            },
        )


@pytest.mark.parametrize(
    "source,target,preparation,verdict",
    [
        ("Plain yogurt", "Vanilla sweetened yogurt", "cold savory sauce", "c_3"),
        ("Vanilla sweetened yogurt", "Plain yogurt", "cold sweet dessert with separately adjusted sugar", "c_2"),
        ("Plain yogurt", "Vanilla sweetened yogurt", "baked savory bread", "c_3"),
        ("Plain yogurt", "Plain Greek yogurt", "cold savory sauce", "c_1"),
    ],
)
def test_transport_fixture_direction_preparation_and_score_never_approve(
    client, monkeypatch, source, target, preparation, verdict
):
    import json

    from modules.planning.services import substitution_reviews as service

    data = setup(client, source_name=source, target_name=target)
    row = review(
        client,
        data,
        context={
            "cooking_role": "binder or creamy dressing",
            "preparation": preparation,
            "restrictions": "Ingredients checked by user; no stated restrictions",
        },
    )
    configure()
    calls = []

    def fixture(db, packet, **kwargs):
        calls.append(packet)
        state = json.loads(packet["state"])
        assert state["from_item"]["name"] == source and state["to_item"]["name"] == target
        assert state["context"]["preparation"] == preparation
        assert "ratio" not in state
        return {
            "status": "ok",
            "provider": "ollama",
            "model": "clef",
            "answers": {
                "q_1": {
                    "choice": verdict,
                    "confidence": 0.999,
                    "probabilities": {key: 0.7 if key == verdict else 0.1 for key in ("c_1", "c_2", "c_3", "c_4")},
                }
            },
        }

    monkeypatch.setattr(service, "run_evaluation", fixture)
    before = counts()
    response = client.post(f"/api/substitution-reviews/{row['id']}/evaluate", json={}, headers=data["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending" and body["ratio"] is None and body["scope"] is None
    assert body["evaluation"]["score"] == 0.7 and body["evaluation"]["below_threshold"]
    assert body["evaluation"]["threshold"] == 0.9 and len(calls) == 1
    assert counts() == before
    assert client.get(f"/api/cook-sessions/{data['session']}").json()["scoped_substitutions"] == []


@pytest.mark.parametrize("mode", ["missing", "disabled", "unavailable", "tie", "config_changed", "source_changed"])
def test_missing_failure_tie_and_changed_evidence_remain_manual(client, monkeypatch, mode):
    from modules.planning.services import substitution_reviews as service

    data = setup(client)
    row = review(client, data, **({"context": {"cooking_role": "seasoning"}} if mode == "missing" else {}))
    calls = []
    if mode != "disabled":
        configure()

    def fixture(db, packet, **kwargs):
        calls.append(packet)
        if mode == "config_changed":
            configure(review_confidence=0.95)
        if mode == "source_changed":
            client.put(f"/api/items/{data['target']}", json={"name": "Different product evidence"})
        return {
            "status": "unavailable" if mode == "unavailable" else "ok",
            "answers": {"q_1": {"choice": "c_1", "probabilities": {"c_1": 0.5, "c_2": 0.5}}} if mode == "tie" else {},
        }

    monkeypatch.setattr(service, "run_evaluation", fixture)
    before = counts()
    response = client.post(f"/api/substitution-reviews/{row['id']}/evaluate", json={}, headers=data["headers"])
    if mode == "source_changed":
        assert response.status_code == 409 and approve(client, row).status_code == 409
    else:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["evaluation"]["provider_requests"] == (0 if mode in {"missing", "disabled"} else 1)
        assert body["evaluation"].get("verdict") is None
        assert approve(client, row).status_code == 200
    assert len(calls) == (0 if mode in {"missing", "disabled"} else 1)
    assert counts() == before


def test_duplicate_original_item_rows_swap_only_the_chosen_row(client):
    data = setup(client)
    replaced = client.put(
        f"/api/recipes/{data['recipe']}/ingredients",
        json=[
            {"item_id": data["source"], "quantity_required": 10, "unit": "g", "optional": False},
            {"item_id": data["source"], "quantity_required": 20, "unit": "g", "optional": True},
        ],
    )
    assert replaced.status_code == 200, replaced.text
    lines = client.get(f"/api/recipes/{data['recipe']}").json()["ingredients"]
    assert len(lines) == 2
    data["ingredient"] = next(line["ingredient_id"] for line in lines if not line["optional"])
    client.post(
        "/api/inventory/add",
        json={"item_id": data["source"], "location_id": data["location"], "quantity": 100, "unit": "g"},
    )
    row = review(client, data)
    assert approve(client, row).status_code == 200
    session = client.get(f"/api/cook-sessions/{data['session']}").json()
    assert {line["item"] for line in session["ingredients"]} == {"Chipotle powder", "Smoked chipotle spice"}
    result = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert result.status_code == 200, result.text
    balances = client.get(f"/api/inventory?location_id={data['location']}").json()["items"]
    by_id = {line["item_id"]: Decimal(str(line["quantity"])) for line in balances}
    # The untouched optional row remains optional and is not auto-deducted.
    assert by_id[data["source"]] == 100 and by_id[data["target"]] == Decimal("37.5")


@pytest.mark.parametrize("after_stock", [False, True])
def test_finish_failure_keeps_one_use_bound_and_never_deducts_twice(client, monkeypatch, after_stock):
    from modules.planning.services import cook_sessions, recipes

    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    before = counts()
    if after_stock:
        original = recipes.remove_inventory

        def fail_after(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic post-stock failure")

        monkeypatch.setattr(recipes, "remove_inventory", fail_after)
        result = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
        assert result.status_code == 400 and "part-way" in result.text
        assert counts()[3] == before[3] + 1
        assert client.get(f"/api/cook-sessions/{data['session']}").json()["status"] == "finishing"
        again = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
        assert again.status_code == 400 and counts()[3] == before[3] + 1
    else:
        original = cook_sessions.cook_recipe

        def fail_before(*args, **kwargs):
            raise ValueError("Synthetic before-stock failure")

        monkeypatch.setattr(cook_sessions, "cook_recipe", fail_before)
        assert client.post(f"/api/cook-sessions/{data['session']}/finish", json={}).status_code == 400
        assert counts() == before
        assert client.get(f"/api/cook-sessions/{data['session']}").json()["status"] == "active"
        monkeypatch.setattr(cook_sessions, "cook_recipe", original)
        assert client.post(f"/api/cook-sessions/{data['session']}/finish", json={}).status_code == 200


def test_global_replace_and_attachment_are_one_atomic_approval(client):
    data = setup(client)
    legacy = client.post(
        "/api/substitutions",
        headers=data["headers"],
        json={"from_item_id": data["source"], "to_item_id": data["target"], "ratio": 2, "unit": "g"},
    )
    assert legacy.status_code == 201
    first = review(client, data)
    assert approve(client, first).status_code == 200
    second = review(client, data)
    response = approve(client, second, scope="global", permanent_ack=True, replace_existing_ack=True)
    assert response.status_code == 409 and "already has" in response.text
    current = client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"][0]
    assert current["from_unit"] is None and Decimal(current["ratio"]) == 2
    pending = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"]
    assert next(row for row in pending if row["id"] == second["id"])["status"] == "pending"


def test_legacy_update_of_basis_bound_global_is_actionable_not_server_error(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row, scope="global", permanent_ack=True).status_code == 200
    current = client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"][0]
    changed = client.put(f"/api/substitutions/{current['id']}", headers=data["headers"], json={"ratio": 9})
    assert changed.status_code == 409 and "replacement review" in changed.text
    preserved = client.get(f"/api/substitutions/for/{data['source']}", headers=data["headers"]).json()["substitutions"][
        0
    ]
    assert Decimal(preserved["ratio"]) == Decimal("1.25") and preserved["from_unit"] == "g"


def test_configuration_change_after_evaluation_hides_pending_hint_but_not_human_choice(client, monkeypatch):
    from modules.planning.services import substitution_reviews as service

    data = setup(client)
    row = review(client, data)
    configure()
    monkeypatch.setattr(
        service,
        "run_evaluation",
        lambda *a, **kw: {"status": "ok", "answers": {"q_1": {"choice": "c_1", "probabilities": {"c_1": 1.0}}}},
    )
    result = client.post(f"/api/substitution-reviews/{row['id']}/evaluate", json={}, headers=data["headers"])
    assert result.status_code == 200 and result.json()["evaluation"]["verdict"] == "c_1"
    configure(review_confidence=0.95)
    current = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"][0]
    assert current["evaluation"]["status"] == "stale_configuration" and "verdict" not in current["evaluation"]
    assert approve(client, row).status_code == 200
    configure(enabled=False)
    assert (
        client.get(f"/api/cook-sessions/{data['session']}").json()["scoped_substitutions"][0]["approval_id"]
        == row["id"]
    )


def test_revoked_shared_recipe_choice_remains_visible_for_explicit_removal(client):
    data = setup(client)
    row = review(client, data)
    assert approve(client, row, scope="recipe").status_code == 200
    later = client.post("/api/cook-sessions", json={"recipe_id": data["recipe"]}).json()["id"]
    saved = client.get(f"/api/cook-sessions/{later}/substitution-reviews").json()["recipe_approvals"][0]
    assert (
        client.post(
            f"/api/cook-sessions/{later}/substitution-reviews/{row['id']}/attach",
            json={"confirmed": True, "expected_binding": saved["binding"]},
        ).status_code
        == 200
    )
    assert client.delete(f"/api/substitution-reviews/{row['id']}").status_code == 204
    listing = client.get(f"/api/cook-sessions/{later}/substitution-reviews").json()
    assert listing["attached_ids"] == [row["id"]]
    assert next(entry for entry in listing["reviews"] if entry["id"] == row["id"])["status"] == "revoked"
    assert listing["recipe_approvals"] == []
    assert client.get(f"/api/cook-sessions/{later}").json()["substitution_review_warning"]
    assert (
        client.patch(f"/api/cook-sessions/{later}", json={"plan": {"substitution_approval_ids": []}}).status_code == 200
    )
    assert client.get(f"/api/cook-sessions/{later}/substitution-reviews").json()["attached_ids"] == []


def test_user_cross_unit_ratio_uses_deterministic_facts_and_invalidates_on_conversion_change(client):
    data = setup(client)
    assert client.put(f"/api/items/{data['target']}", json={"density_g_per_ml": 1.5}).status_code == 200
    row = review(client, data)
    assert row["snapshot"]["to_item"]["unit_evidence"]["effective_density_g_per_ml"] == "1.5000"
    assert approve(client, row, ratio=2, unit="ml").status_code == 200
    client.put(f"/api/items/{data['target']}", json={"density_g_per_ml": 2})
    before = counts()
    assert client.post(f"/api/cook-sessions/{data['session']}/finish", json={}).status_code == 400
    assert counts() == before
    fresh = review(client, data)
    assert approve(client, fresh, ratio=2, unit="ml", replace_approval_ack=True).status_code == 200
    finished = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert finished.status_code == 200, finished.text
    # User approved2ml per1g original:10g*2ml/g*2g/ml =40g; no model conversion.
    balance = client.get(f"/api/inventory?location_id={data['location']}").json()["items"]
    assert Decimal(str(next(line["quantity"] for line in balance if line["item_id"] == data["target"]))) == 10


def test_ratio_precision_and_nonfinite_values_are_rejected_without_rounding_truth(client):
    data = setup(client)
    row = review(client, data)
    before = counts()
    for ratio in ("NaN", "Infinity", 0, -1, 0.00001, 0.12345):
        assert approve(client, row, ratio=ratio).status_code == 422
    assert counts() == before
    assert (
        client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"][0]["status"]
        == "pending"
    )


def test_cascaded_missing_approval_is_visible_and_explicitly_removable(client):
    from sqlalchemy import event

    from app.db.session import engine

    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    engine.dispose()
    event.listen(engine, "connect", enable_foreign_keys)
    try:
        data = setup(client)
        row = review(client, data)
        assert approve(client, row).status_code == 200
        assert client.put(f"/api/recipes/{data['recipe']}/ingredients", json=[]).status_code == 200
        listing = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews")
        assert listing.status_code == 200, listing.text
        assert listing.json()["missing_attachment_ids"] == [row["id"]]
        assert listing.json()["attached_ids"] == [row["id"]]
        assert listing.json()["reviews"] == []
        before = counts()
        assert client.post(f"/api/cook-sessions/{data['session']}/finish", json={}).status_code == 400
        assert counts() == before
        client.put(
            f"/api/recipes/{data['recipe']}/ingredients",
            json=[{"item_id": data["source"], "quantity_required": 10, "unit": "g"}],
        )
        data["ingredient"] = client.get(f"/api/recipes/{data['recipe']}").json()["ingredients"][0]["ingredient_id"]
        fresh = review(client, data)
        blocked = approve(client, fresh)
        assert blocked.status_code == 409 and "remove" in blocked.text.lower()
        removed = client.patch(
            f"/api/cook-sessions/{data['session']}", json={"plan": {"substitution_approval_ids": []}}
        )
        assert removed.status_code == 200, removed.text
        assert (
            client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["missing_attachment_ids"]
            == []
        )
        assert approve(client, fresh).status_code == 200
        assert counts() == before
    finally:
        event.remove(engine, "connect", enable_foreign_keys)
        engine.dispose()


def test_legacy_null_basis_with_archived_source_retains_existing_cook_behavior(client):
    data = setup(client)
    stored = client.post(
        "/api/substitutions",
        headers=data["headers"],
        json={"from_item_id": data["source"], "to_item_id": data["target"], "ratio": 1, "unit": "g"},
    )
    assert stored.status_code == 201 and stored.json()["from_unit"] is None
    assert client.put(f"/api/items/{data['source']}", json={"is_active": False}).status_code == 200
    cooked = client.post(
        f"/api/recipes/{data['recipe']}/cook",
        json={
            "substitutions": [
                {"original_item_id": data["source"], "substitute_item_id": data["target"], "ratio": 1, "unit": "g"}
            ]
        },
    )
    assert cooked.status_code == 200, cooked.text
    balances = client.get(f"/api/inventory?location_id={data['location']}").json()["items"]
    assert Decimal(str(next(line["quantity"] for line in balances if line["item_id"] == data["target"]))) == 40


@pytest.mark.parametrize("action", ["evaluate", "approve", "attach", "revoke", "finish"])
def test_review_commands_lock_recipe_before_review_rows(client, monkeypatch, action):
    from modules.planning import repository as planning_repository
    from modules.planning import substitution_review_repository as repository

    data = setup(client)
    row = review(client, data, context={"cooking_role": "seasoning"})
    if action in {"attach", "finish"}:
        assert approve(client, row, scope="recipe" if action == "attach" else "one_use").status_code == 200
    if action == "attach":
        data["session"] = client.post("/api/cook-sessions", json={"recipe_id": data["recipe"]}).json()["id"]
        row = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["recipe_approvals"][0]
    acquired = []
    original_parent, original_review = planning_repository.lock_recipe_for_update, repository.review

    def parent(db, recipe_id):
        result = original_parent(db, recipe_id)
        acquired.append("recipe")
        return result

    def receipt(db, review_id, *, lock=False):
        result = original_review(db, review_id, lock=lock)
        if lock:
            acquired.append("review")
        return result

    # Observe real repository operations against the fixture DB; no DB results
    # or locks are mocked. PostgreSQL CI separately exercises the cascade race.
    monkeypatch.setattr(planning_repository, "lock_recipe_for_update", parent)
    monkeypatch.setattr(repository, "review", receipt)
    if action == "evaluate":
        response = client.post(f"/api/substitution-reviews/{row['id']}/evaluate", headers=data["headers"])
        assert response.json()["evaluation"]["provider_requests"] == 0
    elif action == "approve":
        response = approve(client, row)
    elif action == "attach":
        response = client.post(
            f"/api/cook-sessions/{data['session']}/substitution-reviews/{row['id']}/attach",
            json={"confirmed": True, "expected_binding": row["binding"]},
        )
    elif action == "revoke":
        response = client.delete(f"/api/substitution-reviews/{row['id']}")
    else:
        response = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert response.status_code in {200, 204}, response.text
    assert "review" in acquired
    assert acquired.index("recipe") < acquired.index("review"), acquired


def test_revoke_finishing_original_session_rejects_before_recipe_or_review_lock(client, monkeypatch):
    from app.db import SessionLocal
    from modules.planning import substitution_review_repository as repository
    from modules.planning.models import CookSession

    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    with SessionLocal() as db:
        db.get(CookSession, UUID(data["session"])).status = "finishing"
        db.commit()
    acquired = []
    original_parent, original_review = repository.lock_recipe, repository.review

    def parent(*args, **kwargs):
        acquired.append("recipe")
        return original_parent(*args, **kwargs)

    def receipt(db, review_id, *, lock=False):
        if lock:
            acquired.append("review")
        return original_review(db, review_id, lock=lock)

    monkeypatch.setattr(repository, "lock_recipe", parent)
    monkeypatch.setattr(repository, "review", receipt)
    before = counts()
    response = client.delete(f"/api/substitution-reviews/{row['id']}")
    assert response.status_code == 409, response.text
    assert "finishing" in response.text
    assert acquired == []
    assert counts() == before
    current = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"][0]
    assert current["status"] == "approved"


def test_ingredient_replacement_locks_parent_before_reading_cascade_children(client, monkeypatch):
    from sqlalchemy import event

    from app.db import SessionLocal
    from modules.planning import repository as planning_repository

    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    observed = []
    original = planning_repository.lock_recipe_for_update

    def parent(db, recipe_id):
        result = original(db, recipe_id)
        observed.append("recipe")
        return result

    def statement(conn, cursor, sql, parameters, context, executemany):
        if "FROM recipe_ingredients" in sql:
            observed.append("ingredients")

    monkeypatch.setattr(planning_repository, "lock_recipe_for_update", parent)
    with SessionLocal() as db:
        engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", statement)
    try:
        response = client.put(
            f"/api/recipes/{data['recipe']}/ingredients",
            json=[{"item_id": data["target"], "quantity_required": 10, "unit": "g"}],
        )
    finally:
        event.remove(engine, "before_cursor_execute", statement)
    assert response.status_code == 200, response.text
    assert observed.index("recipe") < observed.index("ingredients"), observed


@pytest.mark.parametrize("finishing", [False, True])
def test_recipe_delete_prelocks_dependents_nowait_and_preserves_finishing_cook(client, finishing):
    from sqlalchemy import event

    from app.db import SessionLocal
    from modules.planning.models import CookSession

    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    with SessionLocal() as db:
        engine = db.get_bind()
        if finishing:
            db.get(CookSession, UUID(data["session"])).status = "finishing"
            db.commit()
    observed = []

    def query(conn, statement, multiparams, params, execution_options):
        lock = getattr(statement, "_for_update_arg", None)
        if lock is not None:
            observed.extend((table.name, lock.nowait) for table in statement.get_final_froms())

    before = counts()
    event.listen(engine, "before_execute", query)
    try:
        response = client.delete(f"/api/recipes/{data['recipe']}")
    finally:
        event.remove(engine, "before_execute", query)
    assert observed == [("recipes", False), ("cook_sessions", True), ("substitution_reviews", True)]
    if finishing:
        assert response.status_code == 409 and "finishing" in response.text, response.text
        assert counts() == before
        assert client.get(f"/api/recipes/{data['recipe']}").status_code == 200
        listing = client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()
        assert listing["reviews"][0]["status"] == "approved"
    else:
        assert response.status_code == 204, response.text
        assert client.get(f"/api/recipes/{data['recipe']}").status_code == 404


def test_finish_acquires_full_item_union_before_pair_validation(client, monkeypatch):
    from app.db import SessionLocal
    from modules.planning.models.substitution_review import SubstitutionReview
    from modules.planning.services import substitution_reviews as service

    data = setup(client)
    extras = [
        client.post("/api/items", json={"name": name, "stock_mode": "measured", "base_unit": "g"}).json()["id"]
        for name in ("Synthetic pair spice", "Synthetic pair seasoning")
    ]
    ordered = sorted([data["source"], data["target"], *extras])
    first_source, second_source, first_target, second_target = ordered
    for target in (first_target, second_target):
        assert (
            client.post(
                "/api/inventory/add",
                json={"item_id": target, "location_id": data["location"], "quantity": 100, "unit": "g"},
            ).status_code
            == 200
        )
    client.put(
        f"/api/recipes/{data['recipe']}/ingredients",
        json=[
            {"item_id": first_source, "quantity_required": 5, "unit": "g"},
            {"item_id": second_source, "quantity_required": 7, "unit": "g"},
        ],
    )
    lines = client.get(f"/api/recipes/{data['recipe']}").json()["ingredients"]
    first = review(
        client,
        data,
        ingredient_id=next(line["ingredient_id"] for line in lines if line["item_id"] == first_source),
        substitute_item_id=first_target,
    )
    second = review(
        client,
        data,
        ingredient_id=next(line["ingredient_id"] for line in lines if line["item_id"] == second_source),
        substitute_item_id=second_target,
    )
    # Stable review ordering exposes pairs (A,C),(B,D), whose pairwise sorted
    # acquisition otherwise starts A,C,B,D and conflicts with another (B,C) cook.
    with SessionLocal() as db:
        for index, row in enumerate((first, second), 101):
            stored = db.get(SubstitutionReview, UUID(row["id"]))
            stored.id = UUID(int=index)
            row["id"] = str(stored.id)
        db.commit()
    assert approve(client, first).status_code == 200
    assert approve(client, second).status_code == 200
    original = service.item_evidence
    first_locks = []

    def observe(db, item_id, *, lock=False):
        result = original(db, item_id, lock=lock)
        if lock and str(item_id) not in first_locks:
            first_locks.append(str(item_id))
        return result

    monkeypatch.setattr(service, "item_evidence", observe)
    finished = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert first_locks == ordered
    assert len(finished.json()["cook"]["deducted_transaction_ids"]) == 2


def test_approved_finish_does_not_read_or_lock_global_mappings(client, monkeypatch):
    from modules.planning.services import substitution_reviews as service

    data = setup(client)
    row = review(client, data)
    assert approve(client, row).status_code == 200
    lookups = []
    original = service.find_directional

    def observe(*args, **kwargs):
        lookups.append(kwargs.get("lock"))
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "find_directional", observe)
    finished = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert finished.json()["session"]["status"] == "finished"
    assert lookups == []


def test_multiple_row_approvals_do_not_form_a_reconfirmation_loop(client):
    data = setup(client)
    second_source = client.post(
        "/api/items", json={"name": "Ground cumin", "stock_mode": "measured", "base_unit": "g"}
    ).json()["id"]
    second_target = client.post(
        "/api/items", json={"name": "Roasted cumin", "stock_mode": "measured", "base_unit": "g"}
    ).json()["id"]
    client.post(
        "/api/inventory/add",
        json={"item_id": second_target, "location_id": data["location"], "quantity": 40, "unit": "g"},
    )
    client.put(
        f"/api/recipes/{data['recipe']}/ingredients",
        json=[
            {"item_id": data["source"], "quantity_required": 10, "unit": "g"},
            {"item_id": second_source, "quantity_required": 2, "unit": "g"},
        ],
    )
    lines = client.get(f"/api/recipes/{data['recipe']}").json()["ingredients"]
    data["ingredient"] = next(line["ingredient_id"] for line in lines if line["item_id"] == data["source"])
    first = review(client, data)
    assert approve(client, first).status_code == 200
    second = review(
        client,
        data,
        ingredient_id=next(line["ingredient_id"] for line in lines if line["item_id"] == second_source),
        substitute_item_id=second_target,
    )
    assert approve(client, second).status_code == 200
    assert len(client.get(f"/api/cook-sessions/{data['session']}").json()["scoped_substitutions"]) == 2
    finished = client.post(f"/api/cook-sessions/{data['session']}/finish", json={})
    assert finished.status_code == 200, finished.text
    assert len(finished.json()["cook"]["deducted_transaction_ids"]) == 2
    assert all(
        row["status"] == "consumed"
        for row in client.get(f"/api/cook-sessions/{data['session']}/substitution-reviews").json()["reviews"]
    )
