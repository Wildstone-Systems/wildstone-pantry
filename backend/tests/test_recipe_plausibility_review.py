"""Source review protects imports without asserting classifier accuracy."""

from copy import deepcopy

import pytest


def packet():
    return {
        "recipe": {
            "name": "Chipotle beans",
            "instructions": "Mix well",
            "ingredients": [
                {
                    "name": "smoked chipotle powder",
                    "quantity": 1.5,
                    "unit": "tsp",
                    "raw_line": "1 1/2 tsp smoked chipotle powder",
                    "source_line_id": "line:2",
                }
            ],
        },
        "source": {
            "kind": "text",
            "raw_text": "Chipotle beans\nIngredients\n1 1/2 tsp smoked chipotle powder\nMix well\nDirections\nMix well\nUnknown source note",
        },
    }


def test_parse_spans_duplicates_unicode_and_fraction_no_truth_write(client):
    raw = "🌶 Chipotle\r\nIngredients\r\n  • 1 1/2 tsp smoked chipotle powder\r\n1 tsp salt\r\n1 tsp salt\r\nDirections\r\nMix well"
    result = client.post("/api/recipes/parse-text", json={"raw_text": raw})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["source_lines"][0]["role"] == "heading"
    assert data["source_lines"][0]["warnings"] == []
    assert data["raw_text"] == raw
    assert data["ingredients"][0]["quantity"] == 1.5
    assert [r["source_line_id"] for r in data["ingredients"]] == ["line:2", "line:3", "line:4"]
    for line in data["source_lines"]:
        assert raw[line["start"] : line["end"]] == line["raw_line"]
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json() == {"items": []}


@pytest.mark.parametrize(
    "raw, name, warning",
    [
        ("Mix well", "Mix well", "Possible instruction"),
        ("salt and pepper to taste", "salt and pepper", "Compound ingredient"),
        ("flour", "flour", "No explicit source quantity"),
        ("1 tsp p0wd3r", "p0wd3r", "Possible OCR"),
        ("1 tsp smoked ground chipotle", "chipotle", "modifiers missing"),
    ],
)
def test_rules_are_source_linked_advisory_and_dont_write(client, raw, name, warning):
    data = {
        "recipe": {"name": "Test", "ingredients": [{"name": name, "raw_line": raw}]},
        "source": {"kind": "text", "raw_text": raw},
    }
    result = client.post("/api/recipes/import-preview", json=data)
    assert result.status_code == 200, result.text
    preview = result.json()
    assert any(warning in w for w in preview["lines"][0]["warnings"])
    assert preview["provider_requests"] == 0
    assert "Verify every" in preview["warning"]
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json() == {"items": []}


def test_long_description_not_certified_or_hidden_and_legitimate_name_not_instruction(client):
    raw = (
        "Beans\nIngredients\norganic canned white beans in a rich tomato sauce without added salt\nDirections\nMix well"
    )
    parsed = client.post("/api/recipes/parse-text", json={"raw_text": raw}).json()
    line = parsed["source_lines"][2]
    assert line["role"] == "uncertain"
    assert "Unmapped" in line["warnings"][0]
    data = {
        "recipe": {"name": "Test", "ingredients": [{"name": "mixed beans", "raw_line": "mixed beans"}]},
        "source": {"kind": "text", "raw_text": "mixed beans"},
    }
    assert client.post("/api/recipes/import-preview", json=data).json()["lines"][0]["role"] == "ingredient"


def test_confirmation_binding_edit_save_readback_and_legacy(client):
    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    assert client.post("/api/recipes/import-reviewed", json=data).status_code == 422
    changed = deepcopy(data)
    changed["recipe"]["ingredients"][0]["name"] = "chipotle powder"
    rejected = client.post(
        "/api/recipes/import-reviewed", json={**changed, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert rejected.status_code == 409, rejected.text
    assert client.get("/api/items").json() == {"items": []}
    current = client.post("/api/recipes/import-preview", json=changed).json()
    assert any("smoked" in warning for warning in current["lines"][2]["warnings"])
    saved = client.post(
        "/api/recipes/import-reviewed", json={**changed, "confirmed": True, "reviewed_input_hash": current["binding"]}
    )
    assert saved.status_code == 200, saved.text
    recipe_id = saved.json()["id"]
    read = client.get(f"/api/recipes/{recipe_id}").json()
    assert read["ingredients"][0]["item_name"] == "chipotle powder"
    assert float(read["ingredients"][0]["quantity_required"]) == 1.5
    receipt = read["recipe"]["import_review"]
    assert receipt["stale"] is False
    assert receipt["confirmed"] is True
    assert receipt["lines"][2]["source_line_id"] == "line:2"
    assert "raw_line" not in receipt["lines"][2]
    assert read["recipe"]["source_text"] == data["source"]["raw_text"]
    client.put(f"/api/recipes/{recipe_id}", json={"name": "Edited later"})
    assert client.get(f"/api/recipes/{recipe_id}").json()["recipe"]["import_review"]["stale"] is True
    legacy = client.post("/api/recipes", json={"name": "Manual"}).json()
    assert legacy["import_review"] is None


def test_structured_source_paths_external_upsert_and_no_truncation(client):
    recipe = {
        "name": "Soup",
        "image_url": "https://example.com/soup.jpg",
        "external_source": {"source": "test", "id": "123"},
        "ingredients": [{"name": "beans", "quantity": 2, "unit": "cup", "source_line_id": "field:ingredients[0]"}],
    }
    data = {"recipe": recipe, "source": {"kind": "structured", "structured": recipe}}
    first_id = None
    for _ in range(2):
        preview = client.post("/api/recipes/import-preview", json=data).json()
        assert preview["lines"][-1]["field_path"] == "ingredients[0]"
        assert preview["lines"][-1]["start"] is None
        saved = client.post(
            "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
        )
        assert saved.status_code == 200, saved.text
        if first_id:
            assert saved.json()["id"] == first_id
            assert saved.json()["created"] is False
        first_id = saved.json()["id"]
    data = packet()
    for raw in ("a" * 64001, "\n".join(["a"] * 201)):
        data["source"]["raw_text"] = raw
        assert client.post("/api/recipes/import-preview", json=data).status_code == 422


def test_unknown_source_id_and_agent_calls_rejected(client):
    data = packet()
    data["recipe"]["ingredients"][0]["source_line_id"] = "line:199"
    assert client.post("/api/recipes/import-preview", json=data).status_code == 422
    assert client.post("/api/recipes/import-review", json=packet()).status_code == 401
    assert client.post(
        "/api/recipes/import-reviewed", json=packet(), headers={"X-Agent-Key": "anything"}
    ).status_code in (401, 403)


@pytest.fixture
def admin(client):
    credentials = {"email": "recipe-review@example.test", "password": "TestPass123!"}
    assert client.post("/api/auth/bootstrap-admin", json=credentials).status_code == 200
    login = client.post("/api/auth/login", json=credentials)
    return {"Authorization": f"Bearer {login.json()['token']}"}


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
                "api_key": "synthetic-test-key",
                **changes,
            },
        )


def test_disabled_and_unreadable_classifier_zero_calls_manual_save(client, admin, monkeypatch):
    from modules.planning.services import recipe_review

    monkeypatch.setattr(recipe_review, "run_evaluation", lambda *a, **kw: pytest.fail("Unexpected cloud request"))
    result = client.post("/api/recipes/import-review", json=packet(), headers=admin)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "disabled"
    assert result.json()["provider_requests"] == 0
    from app.core.crypto import SecretDecryptError

    def unreadable(db):
        raise SecretDecryptError("Synthetic corrupt configuration")

    monkeypatch.setattr(recipe_review, "classifier_snapshot", unreadable)
    result = client.post("/api/recipes/import-review", json=packet(), headers=admin).json()
    assert result["status"] == "unavailable"
    saved = client.post(
        "/api/recipes/import-reviewed", json={**packet(), "confirmed": True, "reviewed_input_hash": result["binding"]}
    )
    assert saved.status_code == 200, saved.text


def test_synthetic_role_score_not_claimed_accuracy_bounded_context_and_requests(client, admin, monkeypatch):
    import json

    from modules.planning.services import recipe_review

    configure()
    requests = []

    def evaluate(db, payload, **kwargs):
        requests.append(payload)
        return {
            "status": "ok",
            "answers": {
                "q_1": {
                    "choice": "c_2",
                    "confidence": 0.999,
                    "probabilities": {"c_1": 0.2, "c_2": 0.7, "c_3": 0.05, "c_4": 0.03, "c_5": 0.02},
                }
            },
        }

    monkeypatch.setattr(recipe_review, "run_evaluation", evaluate)
    data = packet()
    data["source"]["raw_text"] += "\n" + "\n".join(["Ambiguous food detail"] * 12)
    result = client.post("/api/recipes/import-review", json=data, headers=admin)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["provider_requests"] == len(requests) == 8
    assert all(annotation["score"] == 0.7 and annotation["below_threshold"] for annotation in result["annotations"])
    assert result["annotations"][0]["threshold"] == 0.9
    context = json.loads(requests[0]["state"])
    assert len(context["nearby_source"]) <= 5
    assert context["context_truncated"] is True
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json() == {"items": []}


@pytest.mark.parametrize("status", ["unavailable", "abstain", "tie", "config_changed"])
def test_provider_failure_tie_and_config_drift_never_certify(client, admin, monkeypatch, status):
    from modules.planning.services import recipe_review

    configure()

    def evaluate(db, payload, **kwargs):
        if status == "config_changed":
            configure(review_confidence=0.95)
        return {
            "status": "ok" if status in ("tie", "config_changed") else status,
            "answers": {"q_1": {"choice": "c_1", "probabilities": {"c_1": 0.5, "c_2": 0.5}}} if status == "tie" else {},
        }

    monkeypatch.setattr(recipe_review, "run_evaluation", evaluate)
    result = client.post("/api/recipes/import-review", json=packet(), headers=admin)
    assert result.status_code == 200, result.text
    result = result.json()
    if status == "config_changed":
        assert result["status"] == "stale_configuration"
        assert result["annotations"] == []
    else:
        assert all(a["role"] is None and a["below_threshold"] for a in result["annotations"])
    assert result["lines"][2]["raw_line"].startswith("1 1/2")
    assert client.get("/api/recipes").json() == []


def test_catalog_drift_rejects_ack_and_marks_saved_receipt_stale(client):
    item = client.post("/api/items", json={"name": "smoked chipotle powder"}).json()
    data = packet()
    data["recipe"]["ingredients"][0] = {**data["recipe"]["ingredients"][0], "item_id": item["id"]}
    preview = client.post("/api/recipes/import-preview", json=data).json()
    client.put(f"/api/items/{item['id']}", json={"name": "chipotle powder"})
    rejected = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert rejected.status_code == 409, rejected.text
    preview = client.post("/api/recipes/import-preview", json=data).json()
    saved = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert saved.status_code == 200, saved.text
    client.put(f"/api/items/{item['id']}", json={"name": "renamed again"})
    assert client.get(f"/api/recipes/{saved.json()['id']}").json()["recipe"]["import_review"]["stale"] is True


def test_receipt_failure_rolls_back_catalog_recipe_and_retry_succeeds(client, monkeypatch):
    from modules.planning import recipe_review_repository

    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    original = recipe_review_repository.attach

    def fail(*args):
        raise ValueError("Synthetic receipt storage failure")

    monkeypatch.setattr(recipe_review_repository, "attach", fail)
    response = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert response.status_code == 409
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json() == {"items": []}
    monkeypatch.setattr(recipe_review_repository, "attach", original)
    assert (
        client.post(
            "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
        ).status_code
        == 200
    )


def test_document_parse_and_structured_validate_are_read_only_without_provider(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import Inventory, InventoryTransaction, Item, Recipe, RecipeIngredient
    from modules.planning.services import recipe_review

    monkeypatch.setattr(recipe_review, "run_evaluation", lambda *a, **kw: pytest.fail("Implicit provider request"))
    monkeypatch.setattr(
        "modules.planning.api.recipes.document_bytes_to_text",
        lambda *a, **kw: "Sauce\nIngredients\n1 1/2 cup smoked chipotle powder\nMix well\nDirections\nStir well",
    )
    result = client.post(
        "/api/recipes/parse-image", files={"file": ("synthetic.pdf", b"synthetic fixture", "application/pdf")}
    )
    assert result.status_code == 200, result.text
    assert result.json()["ingredients"][0]["quantity"] == 1.5
    assert any("instruction_as_ingredient" in line["warning_codes"] for line in result.json()["source_lines"])
    result = client.post("/api/recipes/validate", json={"name": "Sauce", "ingredients": [{"name": "salt and pepper"}]})
    assert result.status_code == 200, result.text
    source = result.json()["source_lines"][-1]
    assert "compound_ingredient" in source["warning_codes"]
    assert "default_quantity" in source["warning_codes"]
    with SessionLocal() as db:
        assert all(
            db.query(model).count() == 0 for model in (Item, Recipe, RecipeIngredient, Inventory, InventoryTransaction)
        )


def test_name_resolution_catalog_drift_invalidates_confirmation(client):
    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    item = client.post("/api/items", json={"name": "smoked chipotle powder"})
    assert item.status_code == 201
    rejected = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert rejected.status_code == 409, rejected.text
    assert client.get("/api/recipes").json() == []


def test_catalog_change_during_provider_call_discards_annotations(client, admin, monkeypatch):
    from modules.planning.services import recipe_review

    configure()
    item = client.post("/api/items", json={"name": "smoked chipotle powder"}).json()
    data = packet()
    data["recipe"]["ingredients"][0]["item_id"] = item["id"]

    def evaluate(db, payload, **kwargs):
        client.put(f"/api/items/{item['id']}", json={"name": "changed during fixture call"})
        return {"status": "ok", "answers": {"q_1": {"choice": "c_5", "probabilities": {"c_5": 1.0}}}}

    monkeypatch.setattr(recipe_review, "run_evaluation", evaluate)
    response = client.post("/api/recipes/import-review", json=data, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "stale_source"
    assert response.json()["annotations"] == []


def test_new_catalog_match_at_worker_boundary_rejects_unreviewed_identity(client, monkeypatch):
    from app.db import SessionLocal
    from modules.catalog.schemas import ItemCreate
    from modules.catalog.services.items import create_item
    from modules.planning import recipe_review_repository

    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    original = recipe_review_repository.worker

    def arrival(db):
        with SessionLocal() as concurrent:
            create_item(concurrent, ItemCreate(name="smoked chipotle powder", brand="Unreviewed arrival"))
        return original(db)

    monkeypatch.setattr(recipe_review_repository, "worker", arrival)
    response = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert response.status_code == 409, response.text
    assert client.get("/api/recipes").json() == []
    assert len(client.get("/api/items").json()["items"]) == 1


def test_unexpected_identity_at_actual_resolver_boundary_rolls_back(client, monkeypatch):
    from modules.catalog.schemas import ItemCreate
    from modules.catalog.services.items import create_item
    from modules.planning.services import recipe_writer

    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    original = recipe_writer.resolve_ingredient_specs

    def arrival(db, specs, **kwargs):
        create_item(db, ItemCreate(name="smoked chipotle powder", brand="Unreviewed arrival"))
        return original(db, specs, **kwargs)

    monkeypatch.setattr(recipe_writer, "resolve_ingredient_specs", arrival)
    response = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert response.status_code == 409, response.text
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json()["items"] == []


def test_reviewed_duplicate_absent_names_only_reuse_items_created_by_same_batch(client):
    data = packet()
    duplicate = deepcopy(data["recipe"]["ingredients"][0])
    duplicate["quantity"] = 1
    data["recipe"]["ingredients"].append(duplicate)
    preview = client.post("/api/recipes/import-preview", json=data).json()
    response = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    assert response.status_code == 200, response.text
    read = client.get(f"/api/recipes/{response.json()['id']}").json()
    assert len(read["ingredients"]) == 1
    assert float(read["ingredients"][0]["quantity_required"]) == 2.5
    assert len(client.get("/api/items").json()["items"]) == 1
    assert read["recipe"]["import_review"]["stale"] is False


def test_archived_catalog_evidence_keeps_saved_recipe_readable_and_stale(client):
    data = packet()
    preview = client.post("/api/recipes/import-preview", json=data).json()
    response = client.post(
        "/api/recipes/import-reviewed", json={**data, "confirmed": True, "reviewed_input_hash": preview["binding"]}
    )
    recipe_id = response.json()["id"]
    item_id = client.get(f"/api/recipes/{recipe_id}").json()["ingredients"][0]["item_id"]
    assert client.delete(f"/api/items/{item_id}").status_code in (200, 204)
    read = client.get(f"/api/recipes/{recipe_id}")
    assert read.status_code == 200, read.text
    assert read.json()["recipe"]["import_review"]["stale"] is True


def test_parsed_document_references_can_validate_as_structured_without_mutating_source(client, monkeypatch):
    from modules.planning.services import recipe_review

    monkeypatch.setattr(recipe_review, "run_evaluation", lambda *a, **kw: pytest.fail("Implicit provider request"))
    raw = "Chipotle sauce\nIngredients\n1 1/2 cup smoked chipotle powder\nDirections\nMix well"
    parsed = client.post("/api/recipes/parse-text", json={"raw_text": raw}).json()
    ingredient = {
        key: parsed["ingredients"][0][key]
        for key in ("name", "quantity", "unit", "optional", "raw_line", "source_line_id")
    }
    payload = {
        "name": parsed["title"],
        "instructions": parsed["instructions"],
        "ingredients": [ingredient],
        "source_text": raw,
    }
    assert ingredient["source_line_id"] == "line:2"
    validation = client.post("/api/recipes/validate", json=payload)
    assert validation.status_code == 200, validation.text
    source = validation.json()["source_lines"][-1]
    assert source["source_line_id"] == "field:ingredients[0]"
    assert source["field_path"] == "ingredients[0]"
    assert source["raw_line"] == ingredient["raw_line"]
    assert client.get("/api/recipes").json() == []
    assert client.get("/api/items").json()["items"] == []
    # Real imported source remains document-linked; only synthetic validation rebases.
    preview = client.post(
        "/api/recipes/import-preview", json={"recipe": payload, "source": {"kind": "text", "raw_text": raw}}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["lines"][2]["source_line_id"] == "line:2"
    assert preview.json()["lines"][2]["start"] == raw.index("1 1/2")
    created = client.post("/api/recipes", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["import_review"] is None
