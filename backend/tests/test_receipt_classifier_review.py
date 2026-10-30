"""Synthetic classifier fixtures; real API/database and stock mutations."""

import pytest

from app.db import SessionLocal
from app.models import InventoryTransaction, Item, Receipt
from modules.shopping.services import receipt_review


@pytest.fixture
def context(client):
    credentials = {"email": "draft@example.test", "password": "TestPass123!"}
    client.post("/api/auth/bootstrap-admin", json=credentials)
    token = client.post("/api/auth/login", json=credentials).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    loc = client.post("/api/locations", headers=headers, json={"name": "Pantry"}).json()["id"]
    item = client.post("/api/items", headers=headers, json={"name": "Milk"}).json()["id"]
    return headers, loc, item


def draft(client, context, text="Milk,2,each"):
    h, loc, _ = context
    r = client.post("/api/receipts/draft", headers=h, json={"location_id": loc, "raw_text": text})
    assert r.status_code == 200, r.text
    return r.json()


def action(client, context, d, kind="commit"):
    return client.post(
        f"/api/receipts/drafts/{d['draft_id']}/{kind}",
        headers=context[0],
        json={"token": d["token"], "lines": d["lines"]},
    )


def counts():
    with SessionLocal() as db:
        return tuple(db.query(m).count() for m in (Receipt, InventoryTransaction, Item))


def test_human_confirmation_exact_replay_and_changed_retry(client, context):
    d = draft(client, context)
    assert counts() == (0, 0, 1)
    assert action(client, context, d).status_code == 409
    d["lines"][0]["confirmed"] = True
    r = action(client, context, d)
    assert r.status_code == 200, r.text
    assert action(client, context, d).json() == r.json()
    assert counts() == (1, 1, 1)
    d["lines"][0]["quantity"] = "3"
    assert action(client, context, d).status_code == 409
    assert counts() == (1, 1, 1)


def test_partial_parse_returns_adjustments_unknowns_exclusions(client, context):
    d = draft(client, context, "Milk,2,each\nRETURN Bread -2.99\nBOTTLE DEPOSIT 0.10\n??? CHK BRST\n6 x 200g 2.99")
    assert len(d["lines"]) == 5
    assert d["lines"][1]["role"] == "return"
    assert d["lines"][3]["role"] == "unknown"
    for review_line in d["lines"]:
        review_line.update(confirmed=True, role="stock", matched_item_id=context[2])
    assert action(client, context, d).status_code == 409
    for review_line in d["lines"][1:]:
        review_line["excluded"] = True
    assert action(client, context, d).status_code == 200
    assert counts() == (1, 1, 1)


@pytest.mark.parametrize("change", ["source", "duplicate", "missing", "token", "inactive"])
def test_source_binding_stale_identity_no_writes(client, context, change):
    d = draft(client, context)
    d["lines"][0]["confirmed"] = True
    if change == "source":
        d["lines"][0]["raw_line"] = "another source"
    if change == "duplicate":
        d["lines"] *= 2
    if change == "missing":
        d["lines"][0]["line_id"] = "missing"
    if change == "token":
        d["token"] = "x" * 32
    if change == "inactive":
        client.delete(f"/api/items/{context[2]}", headers=context[0])
    before = counts()
    assert action(client, context, d).status_code in (404, 409)
    assert counts() == before


def test_disabled_manual_completion_and_provenance(client, context):
    d = draft(client, context, "CHK BRST 2.99")
    before = counts()
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    assert r.json()["provider_requests"] == 0
    assert r.json()["annotations"][0]["status"] == "disabled"
    assert "validated accuracy" in r.json()["warning"]
    assert counts() == before
    assert d["lines"][0]["source"] == "rules"
    d["lines"][0].update(matched_item_id=context[2], role="stock", confirmed=True)
    assert action(client, context, d).status_code == 200


def test_synthetic_hints_bounded_advisory_low_score(client, context, monkeypatch):
    d = draft(client, context, "\n".join(f"UNKNOWN {i}" for i in range(10)))
    calls = []
    monkeypatch.setattr(
        receipt_review,
        "config_view",
        lambda db: {
            "valid": True,
            "config": {"enabled": True, "runtime_review_enabled": True, "review_confidence": 0.9},
        },
    )

    def evaluate(db, payload, **kwargs):
        calls.append(payload)
        return {
            "id": "fixture",
            "provider": "ollama",
            "model": "clef",
            "status": "ok",
            "reason_code": "fixture",
            "answers": {
                "q_1": {"choice": "c_1", "confidence": 0.99, "probabilities": {"c_1": 0.65, "c_2": 0.35}},
                "q_2": {"choice": "c_1", "confidence": 0.99, "probabilities": {"c_1": 0.8, "c_21": 0.2}},
            },
        }

    monkeypatch.setattr(receipt_review, "run_evaluation", evaluate)
    before = counts()
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    result = r.json()
    assert len(calls) == 8
    assert result["annotations"][0]["below_threshold"] is True
    assert result["annotations"][-1]["status"] == "limit"
    assert calls[0]["questions"]["q_2"]["criteria"]["c_21"].startswith("Not enough")
    assert action(client, context, d).status_code == 409
    assert counts() == before


def test_second_line_failure_atomic_rollback_and_retry(client, context, monkeypatch):
    d = draft(client, context, "Milk,2,each\nMilk,3,each")
    for review_line in d["lines"]:
        review_line["confirmed"] = True
    from modules.shopping.services import receipts

    original = receipts.add_inventory
    calls = 0

    def fail(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("synthetic second-line failure")
        return original(db, **kwargs)

    monkeypatch.setattr(receipts, "add_inventory", fail)
    r = action(client, context, d)
    assert r.status_code == 409, r.text
    assert counts() == (0, 0, 1)
    monkeypatch.setattr(receipts, "add_inventory", original)
    assert action(client, context, d).status_code == 200
    assert counts() == (1, 2, 1)


@pytest.mark.parametrize("raw", ["Total cereal,1,each", "Total cereal 1 each"])
def test_total_cereal_is_stock_not_summary(client, context, raw):
    d = draft(client, context, raw)
    assert d["lines"][0]["role"] == "stock"
    d["lines"][0].update(confirmed=True, create_new=True, matched_item_id=None)
    assert action(client, context, d).status_code == 200


def test_exact_barcode_preserved_and_never_classified(client, context, monkeypatch):
    h, _, item = context
    client.put(f"/api/items/{item}", headers=h, json={"barcode": "012345678905"})
    d = draft(client, context, "Product 012345678905 2.99")
    assert d["lines"][0]["matched_item_id"] == item
    monkeypatch.setattr(
        receipt_review, "run_evaluation", lambda *a, **k: pytest.fail("exact match must not call classifier")
    )
    assert action(client, context, d, "review").json()["provider_requests"] == 0
    d["lines"][0].update(confirmed=True, quantity="1")
    assert action(client, context, d).status_code == 200


def test_classifier_review_requires_signed_in_admin_and_actor_token(client, context):
    d = draft(client, context, "CHK BRST 2.99")
    body = {"token": d["token"], "lines": d["lines"]}
    r = client.post(f"/api/receipts/drafts/{d['draft_id']}/review", json=body)
    assert r.status_code == 401
    other = {"email": "other@example.test", "password": "TestPass123!"}
    client.post("/api/auth/register", json=other)
    # Wrong token also cannot read a draft even as its real admin actor.
    d["token"] = "y" * 32
    assert action(client, context, d, "review").status_code == 404
    assert counts() == (0, 0, 1)


def test_photo_partial_ocr_retains_raw_unknowns_and_engine(client, context, monkeypatch):
    import modules.automation

    monkeypatch.setattr(
        modules.automation, "image_bytes_to_text", lambda image: "Milk,2,each\n??? CHK\nRETURN Milk -2.00"
    )
    r = client.post(
        f"/api/receipts/draft-image?location_id={context[1]}",
        headers=context[0],
        files={"file": ("receipt.jpg", b"fixture", "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["lines"]) == 3
    assert d["engine"] == "rules"
    assert d["lines"][1]["role"] == "unknown"
    assert d["lines"][2]["role"] == "return"
    assert all(line["source"] == "rules" for line in d["lines"])
    assert counts() == (0, 0, 1)


def test_unavailable_classifier_does_not_prevent_manual_choice(client, context, monkeypatch):
    d = draft(client, context, "CHK BRST 2.99")
    monkeypatch.setattr(
        receipt_review,
        "config_view",
        lambda db: {
            "valid": True,
            "config": {"enabled": True, "runtime_review_enabled": True, "review_confidence": 0.9},
        },
    )
    monkeypatch.setattr(
        receipt_review,
        "run_evaluation",
        lambda *a, **k: {
            "id": "fixture",
            "provider": "ollama",
            "model": "clef",
            "status": "unavailable",
            "reason_code": "timeout",
            "answers": {},
        },
    )
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    assert r.json()["annotations"][0]["status"] == "unavailable"
    d["lines"][0].update(confirmed=True, role="stock", matched_item_id=context[2])
    assert action(client, context, d).status_code == 200


def test_config_change_discards_whole_hint_batch(client, context, monkeypatch):
    d = draft(client, context, "UNKNOWN A\nUNKNOWN B")
    monkeypatch.setattr(
        receipt_review,
        "config_view",
        lambda db: {
            "valid": True,
            "config": {"enabled": True, "runtime_review_enabled": True, "review_confidence": 0.9},
        },
    )
    revision = "one"
    monkeypatch.setattr(receipt_review, "classifier_snapshot", lambda db: {"revision": revision})

    def evaluate(*a, **k):
        nonlocal revision
        revision = "two"
        return {
            "id": "fixture",
            "provider": "ollama",
            "model": "clef",
            "status": "unavailable",
            "reason_code": "timeout",
            "answers": {},
        }

    monkeypatch.setattr(receipt_review, "run_evaluation", evaluate)
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stale_configuration"
    assert r.json()["annotations"] == []
    assert r.json()["provider_requests"] == 1
    assert counts() == (0, 0, 1)


def test_expired_drafts_pruned_on_creation(client, context):
    from datetime import UTC, datetime, timedelta

    from modules.shopping.receipt_review_models import ReceiptReviewDraft

    d = draft(client, context)
    with SessionLocal() as db:
        row = db.query(ReceiptReviewDraft).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert action(client, context, d).status_code == 409
    draft(client, context)
    with SessionLocal() as db:
        assert db.query(ReceiptReviewDraft).count() == 1


def test_negative_extractor_quantity_cannot_be_turned_positive(client, context, monkeypatch):
    from modules.shopping.services import receipts

    def extracted(*a, **k):
        return {
            "engine": "fixture-vision",
            "lines": [
                {
                    "raw_line": "Milk",
                    "name": "Milk",
                    "quantity": "-2",
                    "unit": "each",
                    "matched_item_id": context[2],
                    "source": "ai",
                    "confidence": 0.99,
                }
            ],
        }

    monkeypatch.setattr(receipts, "preview_receipt_image", extracted)
    # The compatibility facade imports the service symbol directly.
    monkeypatch.setattr("app.services.preview_receipt_image", extracted)
    r = client.post(
        f"/api/receipts/draft-image?location_id={context[1]}",
        headers=context[0],
        files={"file": ("synthetic.jpg", b"fixture", "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["engine"] == "fixture-vision"
    assert d["lines"][0]["source"] == "ai"
    assert d["lines"][0]["confidence"] == 0.99
    d["lines"][0].update(quantity="2", role="stock", confirmed=True)
    r = action(client, context, d)
    assert r.status_code == 409, r.text
    assert counts() == (0, 0, 1)


def test_bounded_draft_retention_never_evicts_into_duplicate_commit(client, context):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from modules.shopping import receipt_review_repository as repo
    from modules.shopping.receipt_review_models import ReceiptReviewDraft

    d = draft(client, context)
    with SessionLocal() as db:
        original = db.query(ReceiptReviewDraft).one()
        actor = original.actor_id
        for i in range(100):
            db.add(
                ReceiptReviewDraft(
                    id=uuid4(),
                    actor_id=actor,
                    token_hash="fixture",
                    revision=0,
                    expires_at=datetime.now(UTC) + timedelta(hours=25),
                    document={"fixture": True},
                )
            )
        db.commit()
        repo.create(
            db,
            ReceiptReviewDraft(
                id=uuid4(),
                actor_id=actor,
                token_hash="fixture",
                revision=0,
                expires_at=datetime.now(UTC) + timedelta(hours=26),
                document={"fixture": True},
            ),
        )
        assert db.query(ReceiptReviewDraft).count() == 100
    assert action(client, context, d).status_code == 404
    assert counts() == (0, 0, 1)


def test_no_notifications_escape_failure_or_exact_replay(client, context, monkeypatch):
    from core.events import get_event_bus
    from modules.shopping.services import receipts

    published = []
    monkeypatch.setattr(get_event_bus(), "publish", lambda event, db: published.append((event, counts())))
    d = draft(client, context, "Milk,2,each\nMilk,3,each")
    for line in d["lines"]:
        line["confirmed"] = True
    original = receipts.add_inventory
    calls = 0

    def fail(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("synthetic failure")
        return original(db, **kwargs)

    monkeypatch.setattr(receipts, "add_inventory", fail)
    assert action(client, context, d).status_code == 409
    assert published == []
    monkeypatch.setattr(receipts, "add_inventory", original)
    assert action(client, context, d).status_code == 200
    assert len(published) == 2
    assert all(c == (1, 2, 1) for event, c in published)
    assert action(client, context, d).status_code == 200
    assert len(published) == 2


def test_known_nonstock_rules_and_unrelated_catalog_not_offered(client, context, monkeypatch):
    d = draft(client, context, "TOTAL 2.99\nRETURN Milk -2.00\nCHK BRST 2.99")
    monkeypatch.setattr(
        receipt_review, "run_evaluation", lambda *a, **k: pytest.fail("disabled classifier must never call provider")
    )
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    annotations = r.json()["annotations"]
    assert [a["status"] for a in annotations] == ["rules", "rules", "disabled"]
    assert all(a["candidates"] == [] for a in annotations)
    assert r.json()["provider_requests"] == 0
    assert counts() == (0, 0, 1)


def test_automation_expected_revision_rejects_before_transport(context, monkeypatch):
    from modules.automation import classifier_service

    monkeypatch.setattr(
        classifier_service.clef, "evaluate", lambda *a: pytest.fail("stale revision must not call provider")
    )
    with SessionLocal() as db:
        result = classifier_service.run_evaluation(
            db,
            {
                "state": "synthetic apple",
                "questions": {
                    "q_1": {
                        "type": "choice",
                        "instructions": "Choose supported identity",
                        "criteria": {"c_1": "apple", "c_2": "unknown"},
                    }
                },
            },
            expected_revision="stale-fixture",
        )
    assert result["status"] == "abstain"
    assert result["reason_code"] == "stale_configuration"


def test_corrupted_config_is_unavailable_not_server_error(client, context, monkeypatch):
    from app.core.crypto import encrypt_json
    from app.models import ConnectorConfig

    d = draft(client, context, "CHK BRST 2.99")
    with SessionLocal() as db:
        db.add(ConnectorConfig(key="classifier", ciphertext=encrypt_json({"enabled": None})))
        db.commit()
    monkeypatch.setattr(
        receipt_review, "run_evaluation", lambda *a, **k: pytest.fail("corrupted settings must not call provider")
    )
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "unavailable"
    assert r.json()["provider_requests"] == 0
    d["lines"][0].update(confirmed=True, role="stock", matched_item_id=context[2])
    assert action(client, context, d).status_code == 200


def test_receipt_openapi_has_versioned_draft_and_review_output(client, context):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in ("/api/receipts/draft", "/api/receipts/draft-image"):
        output = paths[path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert output["$ref"].endswith("/ReceiptDraftRead")
    output = paths["/api/receipts/drafts/{draft_id}/review"]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert output["$ref"].endswith("/ReceiptClassifierReviewRead")
    assert "source" in schema["components"]["schemas"]["ReceiptDraftLineRead"]["properties"]
    assert "role_score" in schema["components"]["schemas"]["ReceiptAnnotationRead"]["properties"]


def test_dollar_before_minus_return_cannot_be_positive_stock(client, context):
    d = draft(client, context, "Milk $-2.99")
    assert d["lines"][0]["role"] == "return"
    d["lines"][0].update(confirmed=True, role="stock", matched_item_id=context[2], quantity="1")
    assert action(client, context, d).status_code == 409
    assert counts() == (0, 0, 1)


def test_candidate_packet_uses_saved_catalog_context_without_inventing_missing_facts(client, context, monkeypatch):
    import json

    from modules.automation.classifier_contract import EvaluationRequest

    h, _, item = context
    assert (
        client.put(
            f"/api/items/{item}",
            headers=h,
            json={
                "brand": "Synthetic dairy",
                "ingredients_text": "Milk, vitamin D",
                "energy_kcal_100g": "60",
                "protein_g_100g": "3.2",
            },
        ).status_code
        == 200
    )
    d = draft(client, context, "Milk,1,each")
    d["lines"][0]["matched_item_id"] = None
    monkeypatch.setattr(
        receipt_review,
        "config_view",
        lambda db: {
            "valid": True,
            "config": {"enabled": True, "runtime_review_enabled": True, "review_confidence": 0.9},
        },
    )
    packets = []

    def evaluate(db, payload, **kwargs):
        EvaluationRequest.model_validate(payload)
        packets.append(json.loads(payload["state"]))
        return {
            "id": "fixture",
            "provider": "ollama",
            "model": "clef",
            "status": "abstain",
            "reason_code": "fixture",
            "answers": {},
        }

    monkeypatch.setattr(receipt_review, "run_evaluation", evaluate)
    r = action(client, context, d, "review")
    assert r.status_code == 200, r.text
    candidate = packets[0]["candidate_context"]["c_1"]
    assert candidate["brand"] == "Synthetic dairy"
    assert candidate["ingredients_text"] == "Milk, vitamin D"
    assert candidate["nutrition_values"]["protein_g_100g"] == "3.20"
    assert candidate["saved_product_facts"] == {}
    assert candidate["saved_facts_provenance"] == "unknown"
    assert "Unknown" in candidate["missing_facts"]
    assert counts() == (0, 0, 1)


@pytest.mark.parametrize("raw", ["AMOUNT DUE 2.99", "Discount -2.00", "BOTTLE DEPOSIT 0.10"])
def test_financial_marker_parity_cannot_be_changed_to_positive_stock(client, context, raw):
    d = draft(client, context, raw)
    assert d["lines"][0]["role"] == "financial_adjustment"
    d["lines"][0].update(role="stock", confirmed=True, matched_item_id=context[2], quantity="1")
    assert action(client, context, d).status_code == 409
    assert counts() == (0, 0, 1)


@pytest.mark.parametrize("name", ["", "   "])
@pytest.mark.parametrize("excluded", [False, True])
def test_blank_edited_names_are_accepted_only_for_excluded_rows(client, context, name, excluded):
    d = draft(client, context, "Milk,1,each\nMilk,2,each")
    d["lines"][0]["confirmed"] = True
    d["lines"][1].update(name=name, excluded=excluded, confirmed=True)
    response = action(client, context, d)
    if excluded:
        assert response.status_code == 200, response.text
        assert response.json()["line_count"] == 1
        assert response.json()["lines"][0]["raw_line"] == "Milk,1,each"
        assert response.json()["lines"][0]["parsed_quantity"] == "1.000"
        assert counts() == (1, 1, 1)
    else:
        assert response.status_code == 409, response.text
        assert counts() == (0, 0, 1)
