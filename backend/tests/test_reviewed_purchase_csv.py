"""Real API/SQLite regressions; classifier responses are synthetic fixtures."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.db import SessionLocal
from app.models import InventoryTransaction, Item, Purchase, PurchaseLineItem
from modules.shopping.purchase_review_models import PurchaseCsvClaim, PurchaseReviewDraft
from modules.shopping.services import purchase_review, purchases


@pytest.fixture
def context(client):
    credentials = {"email": "csv-review@example.com", "password": "SyntheticCsvReview987!"}
    client.post("/api/auth/bootstrap-admin", json=credentials)
    token = client.post("/api/auth/login", json=credentials).json()["token"]
    h = {"Authorization": "Bearer " + token}
    loc = client.post("/api/locations", headers=h, json={"name": "CSV pantry"}).json()["id"]
    milk = client.post("/api/items", headers=h, json={"name": "Milk", "barcode": "012345678905"}).json()["id"]
    bread = client.post("/api/items", headers=h, json={"name": "Bread"}).json()["id"]
    return h, loc, milk, bread


def draft(client, context, csv="name,quantity,unit\nMilk,2,each\nBread,1,each", **options):
    response = client.post(
        "/api/purchases/draft-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv, **options}
    )
    assert response.status_code == 200, response.text
    return response.json()


def act(client, context, d, kind="commit"):
    return client.post(
        f"/api/purchases/drafts/{d['draft_id']}/{kind}",
        headers=context[0],
        json={"token": d["token"], "fingerprint": d["fingerprint"], "lines": d["lines"]},
    )


def verify(d):
    for line in d["lines"]:
        line["confirmed"] = True


def counts():
    with SessionLocal() as db:
        return tuple(
            db.query(model).count()
            for model in (Purchase, PurchaseLineItem, InventoryTransaction, Item, PurchaseCsvClaim)
        )


def test_preview_and_predictions_write_no_truth_and_human_choice_survives(client, context):
    d = draft(client, context)
    assert counts() == (0, 0, 0, 2, 0)
    assert act(client, context, d).status_code == 409
    d["lines"][0]["matched_item_id"] = context[3]
    d["lines"][1]["excluded"] = True
    verify(d)
    r = act(client, context, d)
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["matched_item_id"] == context[3]
    assert counts() == (1, 1, 1, 2, 1)
    assert act(client, context, d).json() == r.json()
    d["lines"][0]["quantity"] = "3"
    assert act(client, context, d).status_code == 409
    assert counts() == (1, 1, 1, 2, 1)


def test_barcode_wins_and_conflicting_selection_rejected(client, context):
    d = draft(client, context, "name,quantity,unit,barcode\nBread,1,each,0012345678905")
    assert d["lines"][0]["matched_item_id"] == context[2]
    assert d["lines"][0]["resolution"] == "barcode"
    verify(d)
    d["lines"][0]["matched_item_id"] = context[3]
    assert act(client, context, d).status_code == 409
    assert counts() == (0, 0, 0, 2, 0)
    d["lines"][0]["matched_item_id"] = context[2]
    assert act(client, context, d).status_code == 200


@pytest.mark.parametrize("quantity", ["-2", "0", "-0"])
def test_nonpositive_source_cannot_be_promoted_to_stock(client, context, quantity):
    d = draft(client, context, f"name,quantity,unit\nMilk,{quantity},each\nBread,1,each")
    assert d["lines"][0]["source_quantity"] == quantity
    assert d["lines"][0]["source_nonstock"] is True
    d["lines"][0].update(quantity="5", role="stock")
    verify(d)
    assert act(client, context, d).status_code == 409
    d["lines"][0]["excluded"] = True
    assert act(client, context, d).status_code == 200
    assert counts() == (1, 1, 1, 2, 1)


@pytest.mark.parametrize("quantity", ["not-a-number", "NaN", "Infinity", ""])
def test_invalid_quantity_is_visible_and_requires_human_correction(client, context, quantity):
    d = draft(client, context, f"name,quantity,unit\nMilk,{quantity},each")
    assert d["lines"][0]["quantity"] == quantity
    assert d["lines"][0]["warnings"]
    verify(d)
    assert act(client, context, d).status_code == 409
    d["lines"][0].update(quantity="2", role="stock")
    assert act(client, context, d).status_code == 200


def test_duplicate_drafts_and_legacy_csv_share_source_claim(client, context):
    csv = "name,quantity,unit\nMilk,2,each"
    first = draft(client, context, csv)
    second = draft(client, context, csv)
    verify(first)
    verify(second)
    assert act(client, context, first).status_code == 200
    assert act(client, context, second).status_code == 409
    duplicate = client.post(
        "/api/purchases/import-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert duplicate.status_code == 409
    assert draft(client, context, csv)["duplicate_of_purchase_id"]
    assert counts() == (1, 1, 1, 2, 1)


def test_legacy_then_reviewed_duplicate_is_rejected(client, context):
    csv = "name,quantity,unit\nMilk,1,each"
    response = client.post(
        "/api/purchases/import-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert response.status_code == 200, response.text
    d = draft(client, context, csv)
    verify(d)
    assert d["duplicate_of_purchase_id"] == response.json()["purchase_id"]
    assert act(client, context, d).status_code == 409


def test_rollback_releases_claim_and_defers_events_until_success(client, context, monkeypatch):
    from core.events import get_event_bus

    d = draft(client, context)
    verify(d)
    calls = []
    published = []
    original = purchase_review.add_inventory

    def fail_second(db, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise ValueError("Synthetic second-row failure")
        return original(db, **kwargs)

    monkeypatch.setattr(purchase_review, "add_inventory", fail_second)
    monkeypatch.setattr(get_event_bus(), "publish", lambda event, db=None: published.append(counts()))
    assert act(client, context, d).status_code == 409
    assert counts() == (0, 0, 0, 2, 0) and published == []
    monkeypatch.setattr(purchase_review, "add_inventory", original)
    result = act(client, context, d)
    assert result.status_code == 200, result.text
    assert published == [(1, 2, 2, 2, 1)] * 2
    assert act(client, context, d).status_code == 200
    assert len(published) == 2


@pytest.mark.parametrize(
    "change", ["raw", "line_id", "fingerprint", "barcode", "missing", "duplicate", "token", "archived", "expiry"]
)
def test_stale_or_forged_choices_never_write(client, context, change):
    d = draft(client, context)
    verify(d)
    if change == "raw":
        d["lines"][0]["raw_line"] = "other source"
    if change == "line_id":
        d["lines"][0]["line_id"] = "other"
    if change == "fingerprint":
        d["fingerprint"] = "f" * 64
    if change == "barcode":
        d["lines"][0]["barcode"] = "123"
    if change == "missing":
        d["lines"].pop()
    if change == "duplicate":
        d["lines"][1] = copy.deepcopy(d["lines"][0])
    if change == "token":
        d["token"] = "x" * 32
    if change == "archived":
        client.delete(f"/api/items/{context[2]}", headers=context[0])
    if change == "expiry":
        with SessionLocal() as db:
            row = db.get(PurchaseReviewDraft, UUID(d["draft_id"]))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
    before = counts()
    assert act(client, context, d).status_code in (404, 409)
    assert counts() == before


def test_create_new_reuses_canonical_item_and_batch_duplicates(client, context):
    d = draft(client, context, "name,quantity,unit\nMilk,1,each\nMilk,2,each")
    for line in d["lines"]:
        line.update(matched_item_id=None, create_new=True, confirmed=True)
    assert act(client, context, d).status_code == 200
    assert counts() == (1, 2, 2, 2, 1)


def test_excluded_blank_name_and_missing_name_source_are_preserved(client, context):
    d = draft(client, context, "name,quantity,unit\nMilk,1,each\n,1,each")
    assert d["lines"][1]["name"] == ""
    d["lines"][0]["confirmed"] = True
    d["lines"][1]["excluded"] = True
    assert act(client, context, d).status_code == 200
    assert counts() == (1, 1, 1, 2, 1)


def test_metadata_options_have_distinct_binding_but_shared_source_identity(client, context):
    a = draft(client, context, apply_to_inventory=False)
    b = draft(client, context, apply_to_inventory=True)
    assert a["fingerprint"] != b["fingerprint"]
    assert a["source_fingerprint"] == b["source_fingerprint"]
    verify(a)
    a["fingerprint"] = b["fingerprint"]
    assert act(client, context, a).status_code == 409


def test_classifier_disabled_manual_fallback_and_no_choice_mutation(client, context):
    d = draft(client, context, "name,quantity,unit\nUnknown yogurt,2,each")
    before = counts()
    r = act(client, context, d, "review")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == "purchase-review.v1"
    assert r.json()["provider_requests"] == 0
    assert counts() == before
    d["lines"][0].update(matched_item_id=context[2], confirmed=True)
    assert act(client, context, d).status_code == 200


@pytest.mark.parametrize("quantity", ["-1", "0", "bad", "NaN"])
def test_legacy_truth_entrypoint_rejects_nonpositive_or_invalid_without_writes(client, context, quantity):
    r = client.post(
        "/api/purchases/import-csv",
        headers=context[0],
        json={"location_id": context[1], "csv_text": f"name,quantity,unit\nMilk,{quantity},each"},
    )
    assert r.status_code == 409, r.text
    assert counts() == (0, 0, 0, 2, 0)


def test_legacy_csv_and_receipt_use_the_same_canonical_resolver(client, context):
    generic = client.post("/api/items", headers=context[0], json={"name": "Ground cumin"}).json()["id"]
    csv = "name,quantity,unit\nMcCormick cumin ground 8 oz,1,each"
    d = draft(client, context, csv)
    assert d["lines"][0]["matched_item_id"] == generic
    receipt = client.post(
        "/api/receipts/preview",
        headers=context[0],
        json={"location_id": context[1], "raw_text": "McCormick cumin ground 8 oz,1,each"},
    )
    assert receipt.json()["lines"][0]["matched_item_id"] == generic
    legacy = client.post(
        "/api/purchases/import-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["lines"][0]["matched_item_id"] == generic
    assert counts()[3] == 3


def test_historical_duplicate_sources_preview_without_deleting_history(client, context):
    csv = "name,quantity,unit\nMilk,1,each"
    fp = purchases.compute_purchase_fingerprint(
        csv_text=csv, merchant=None, purchased_on=None, location_id=UUID(context[1])
    )
    with SessionLocal() as db:
        old = Purchase(
            location_id=UUID(context[1]),
            source="csv",
            source_fingerprint=fp,
            status="processed",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer = Purchase(
            location_id=UUID(context[1]),
            source="csv",
            source_fingerprint=fp,
            status="processed",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        db.add_all([old, newer])
        db.commit()
        expected = str(old.id)
    legacy = client.post(
        "/api/purchases/preview-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["duplicate_of_purchase_id"] == expected
    assert draft(client, context, csv)["duplicate_of_purchase_id"] == expected
    assert counts() == (2, 0, 0, 2, 0)


def test_legacy_failure_rolls_back_all_rows_claims_and_events(client, context, monkeypatch):
    from core.events import get_event_bus

    original = purchases.add_inventory
    calls = []
    events = []

    def fail(db, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise ValueError("Synthetic legacy failure")
        return original(db, **kwargs)

    monkeypatch.setattr(purchases, "add_inventory", fail)
    monkeypatch.setattr(get_event_bus(), "publish", lambda event, db=None: events.append(event))
    body = {"location_id": context[1], "csv_text": "name,quantity,unit\nMilk,2,each\nBread,1,each"}
    assert client.post("/api/purchases/import-csv", headers=context[0], json=body).status_code == 409
    assert counts() == (0, 0, 0, 2, 0) and events == []
    monkeypatch.setattr(purchases, "add_inventory", original)
    assert client.post("/api/purchases/import-csv", headers=context[0], json=body).status_code == 200
    assert len(events) == 2


def test_actor_and_admin_inference_boundaries(client, context):
    d = draft(client, context, "name,quantity,unit\nUnknown yogurt,2,each")
    body = {"token": d["token"], "fingerprint": d["fingerprint"], "lines": d["lines"]}
    url = f"/api/purchases/drafts/{d['draft_id']}"
    assert client.post(url + "/review", json=body).status_code == 401
    secret = client.post(
        "/api/agent-keys", headers=context[0], json={"name": "Synthetic CSV rejected key", "scopes": "read,write"}
    ).json()["secret"]
    assert client.post(url + "/review", headers={**context[0], "X-Agent-Key": secret}, json=body).status_code == 403
    assert client.post(url + "/commit", headers={**context[0], "X-Agent-Key": secret}, json=body).status_code == 403
    credentials = {"email": "another-admin@example.com", "password": "SyntheticOther987!", "role": "admin"}
    assert client.post("/api/auth/users", headers=context[0], json=credentials).status_code == 200
    token = client.post("/api/auth/login", json=credentials).json()["token"]
    other = {"Authorization": "Bearer " + token}
    assert client.post(url + "/review", headers=other, json=body).status_code == 404
    assert client.post(url + "/commit", headers=other, json=body).status_code == 404
    assert counts() == (0, 0, 0, 2, 0)


def test_history_only_and_disallowed_item_creation_flags_are_bound(client, context):
    d = draft(client, context, "name,quantity,unit\nMilk,2,each", apply_to_inventory=False)
    verify(d)
    result = act(client, context, d)
    assert result.status_code == 200 and result.json()["inventory_transaction_ids"] == []
    assert counts() == (1, 1, 0, 2, 1)
    other = draft(client, context, "name,quantity,unit\nNew spice,2,each", create_missing_items=False)
    other["lines"][0].update(create_new=True, confirmed=True)
    assert act(client, context, other).status_code == 409
    assert counts() == (1, 1, 0, 2, 1)


def test_raw_quoted_multiline_source_and_openapi_outputs(client, context):
    csv = 'name,quantity,unit\n"Milk, plain\nfamily",1,each'
    d = draft(client, context, csv)
    assert d["lines"][0]["raw_line"] == '"Milk, plain\nfamily",1,each'
    schema = client.get("/openapi.json").json()
    for path, model in [
        ("/api/purchases/draft-csv", "PurchaseDraftRead"),
        ("/api/purchases/drafts/{draft_id}/review", "PurchaseClassifierReviewRead"),
    ]:
        assert schema["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith(model)


@pytest.mark.parametrize(
    "csv",
    ["name,quantity,quantity\nMilk,1,2", 'name,quantity\n"unclosed,1', "name,quantity,unit\n" + "x" * 4001 + ",1,each"],
)
def test_malformed_or_oversized_sources_rejected_before_draft_storage(client, context, csv):
    r = client.post("/api/purchases/draft-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv})
    assert r.status_code == 422, r.text
    legacy = client.post(
        "/api/purchases/preview-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert legacy.status_code == 422, legacy.text
    with SessionLocal() as db:
        assert db.query(PurchaseReviewDraft).count() == 0
    assert counts() == (0, 0, 0, 2, 0)


def test_barcode_created_while_waiting_for_identity_lock_is_revalidated(client, context, monkeypatch):
    from modules.catalog.schemas import ItemUpdate
    from modules.catalog.services.items import get_item, update_item

    d = draft(client, context, "name,quantity,unit,barcode\nMilk,1,each,0098765432105")
    verify(d)
    original = purchase_review.lock_import_items

    def change_at_lock(db, rows):
        original(db, rows)
        # Real catalog write at the coordination point, not a mocked DB result.
        update_item(db, get_item(db, UUID(context[3])), ItemUpdate(barcode="0098765432105"))

    monkeypatch.setattr(purchase_review, "lock_import_items", change_at_lock)
    assert act(client, context, d).status_code == 409
    assert counts() == (0, 0, 0, 2, 0)


def test_optional_csv_hints_receive_csv_context_and_never_apply_predictions(client, context, monkeypatch):
    import json

    from modules.shopping.services import receipt_review

    d = draft(client, context, "name,quantity,unit\nMLK,2,each\nBread,-1,each")
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
        packets.append(json.loads(payload["state"]))
        return {
            "id": "synthetic",
            "status": "ok",
            "reason_code": "fixture",
            "provider": "ollama",
            "model": "clef",
            "answers": {
                "q_1": {"choice": "c_1", "probabilities": {"c_1": 0.6, "c_2": 0.2, "c_3": 0.1, "c_4": 0.1}},
                "q_2": {"choice": "c_1", "probabilities": {"c_1": 0.7, "c_20": 0.1, "c_21": 0.2}},
            },
        }

    monkeypatch.setattr(receipt_review, "run_evaluation", evaluate)
    before = counts()
    response = act(client, context, d, "review")
    assert response.status_code == 200, response.text
    assert response.json()["provider_requests"] == 1
    assert "Purchase CSV export row" in packets[0]["context"]
    assert response.json()["annotations"][0]["below_threshold"] is True
    assert act(client, context, d).status_code == 409
    assert counts() == before


def test_earlier_candidate_change_during_later_row_discards_entire_hint_batch(client, context, monkeypatch):
    from modules.catalog.schemas import ItemUpdate
    from modules.catalog.services.items import get_item, update_item
    from modules.shopping.services import receipt_review

    d = draft(client, context, "name,quantity,unit\nMLK,2,each\nBRED,1,each")
    monkeypatch.setattr(
        receipt_review,
        "config_view",
        lambda db: {
            "valid": True,
            "config": {"enabled": True, "runtime_review_enabled": True, "review_confidence": 0.9},
        },
    )
    calls = []

    def evaluate(db, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 2:
            update_item(
                db,
                get_item(db, UUID(context[2])),
                ItemUpdate(ingredients_text="Changed while the later row is being evaluated"),
            )
        return {
            "id": "synthetic",
            "status": "ok",
            "reason_code": "fixture",
            "provider": "ollama",
            "model": "clef",
            "answers": {
                "q_1": {"choice": "c_1", "probabilities": {"c_1": 0.9, "c_2": 0.1}},
                "q_2": {"choice": "c_1", "probabilities": {"c_1": 0.9, "c_21": 0.1}},
            },
        }

    monkeypatch.setattr(receipt_review, "run_evaluation", evaluate)
    response = act(client, context, d, "review")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "stale_source"
    assert response.json()["annotations"] == []
    assert response.json()["provider_requests"] == 2
    assert counts() == (0, 0, 0, 2, 0)


def test_legacy_and_reviewed_preview_use_identical_deterministic_alias_column_precedence(client, context):
    csv = "name,description,quantity,qty,unit\nMilk,Bread,2,99,each"
    d = draft(client, context, csv)
    assert d["lines"][0]["name"] == "Milk" and d["lines"][0]["quantity"] == "2"
    preview = client.post(
        "/api/purchases/preview-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["lines"][0]["matched_item_id"] == context[2]
    assert Decimal(preview.json()["lines"][0]["parsed_quantity"]) == 2
    response = client.post(
        "/api/purchases/import-csv", headers=context[0], json={"location_id": context[1], "csv_text": csv}
    )
    assert response.status_code == 200, response.text
    assert response.json()["lines"][0]["matched_item_id"] == context[2]
    assert Decimal(response.json()["lines"][0]["parsed_quantity"]) == 2


@pytest.mark.parametrize("second_barcode", ["0098765432105", "00098765432105"])
def test_same_batch_created_barcode_conflict_rolls_back_catalog_stock_claim_and_notifications(
    client, context, monkeypatch, second_barcode
):
    from core.events import get_event_bus

    d = draft(
        client, context, f"name,quantity,unit,barcode\nNew spice,1,each,0098765432105\nMilk,2,each,{second_barcode}"
    )
    d["lines"][0].update(create_new=True, matched_item_id=None)
    verify(d)
    published = []
    monkeypatch.setattr(get_event_bus(), "publish", lambda event, db=None: published.append(event))
    response = act(client, context, d)
    assert response.status_code == 409, response.text
    assert counts() == (0, 0, 0, 2, 0)
    assert published == []
    # Correcting the conflicting choice can retry the rolled-back draft safely.
    d["lines"][1].update(create_new=True, matched_item_id=None)
    corrected = act(client, context, d)
    assert corrected.status_code == 200, corrected.text
    assert len({line["matched_item_id"] for line in corrected.json()["lines"]}) == 1
    assert counts() == (1, 2, 2, 3, 1)
    assert len(published) == 2


@pytest.mark.parametrize("second_barcode", ["0098765432105", "00098765432105"])
def test_compatible_same_batch_create_choices_reuse_one_barcode_identity(client, context, second_barcode):
    d = draft(
        client,
        context,
        f"name,quantity,unit,barcode\nNew spice,1,each,0098765432105\nOther label,2,each,{second_barcode}",
    )
    for line in d["lines"]:
        line.update(create_new=True, matched_item_id=None, confirmed=True)
    response = act(client, context, d)
    assert response.status_code == 200, response.text
    assert len(response.json()["created_item_ids"]) == 1
    assert len({line["matched_item_id"] for line in response.json()["lines"]}) == 1
    assert counts() == (1, 2, 2, 3, 1)


@pytest.mark.parametrize("reuse_create_choice", [False, True])
def test_same_unknown_barcode_cannot_be_split_between_two_existing_items(client, context, reuse_create_choice):
    d = draft(client, context, "name,quantity,unit,barcode\nBread,1,each,0098765432105\nMilk,2,each,0098765432105")
    if reuse_create_choice:
        d["lines"][0].update(create_new=True, matched_item_id=None)
    verify(d)
    response = act(client, context, d)
    assert response.status_code == 409, response.text
    assert counts() == (0, 0, 0, 2, 0)


def test_legacy_same_unknown_barcode_conflicting_named_items_rolls_back(client, context):
    response = client.post(
        "/api/purchases/import-csv",
        headers=context[0],
        json={
            "location_id": context[1],
            "csv_text": "name,quantity,unit,barcode\nBread,1,each,0098765432105\nMilk,2,each,0098765432105",
        },
    )
    assert response.status_code == 409, response.text
    assert counts() == (0, 0, 0, 2, 0)
