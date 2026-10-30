"""Explicit AI-draft version transitions. Test actors do not supply human labels."""

import copy
from uuid import UUID

import pytest
from sqlalchemy import select
from test_classifier_benchmark import BASE, POLICY, configure, prepared, run

from app.db.session import SessionLocal
from app.models import ConnectorConfig
from modules.automation import benchmark_repository as repository
from modules.automation import benchmark_service as service
from modules.automation.benchmark_contract import PREVIOUS_CORPUS_HASH
from modules.automation.benchmark_corpus import RUBRICS, digest
from modules.automation.models import ClassifierBenchmark


@pytest.fixture
def admin(client):
    client.post(
        "/api/auth/bootstrap-admin", json={"email": "corpus-revision@example.com", "password": "CorpusRevision987!"}
    )
    response = client.post(
        "/api/auth/login", json={"email": "corpus-revision@example.com", "password": "CorpusRevision987!"}
    )
    return {"Authorization": "Bearer " + response.json()["token"]}


def old_unreviewed_draft():
    with SessionLocal() as db:
        repository.create(
            db,
            "holdout_marker",
            {"corpus_hash": PREVIOUS_CORPUS_HASH, "exposed": False},
            key=service._marker_id(PREVIOUS_CORPUS_HASH),
        )
        repository.create(
            db,
            "review",
            {
                "corpus_hash": PREVIOUS_CORPUS_HASH,
                "labels": {key: case["proposed_label"] for key, case in service.CASES.items()},
                "reviewed": {},
                "frozen": False,
                "policy": None,
                "holdout_exposed": False,
            },
            key=repository.REVIEW_ID,
        )


def test_revised_proposals_keep_strict_rubrics_and_transfer_controls():
    assert service.CORPUS["version"] == "synthetic-draft.v2"
    assert digest(RUBRICS) == "81e112d57e53bdb3e6c7f45f1e74c3c95bbabd7afc6ccb9ade319f08225dff60"
    corrections = {
        "physical_form.10": "insufficient",
        "receipt_ambiguity.15": "insufficient",
        "approval_scope.16": "insufficient",
        "quantity_roles.12": "review_required",
    }
    for key, label in corrections.items():
        assert service.CASES[key]["proposed_label"] == label
        assert service.CASES[key]["provenance"] == "ai_proposed"
    paraphrase_groups = {"train": set(), "holdout": set()}
    for case in service.CASES.values():
        if case["task"] in {"nutrition_transfer", "density_transfer"} and case["proposed_label"] == "insufficient":
            assert case["critical_negative"]
        if (
            case["proposed_label"] == "eligible"
            and case["evidence"]["subject"] != case["evidence"]["candidate_or_context"]
        ):
            paraphrase_groups[case["split"]].add(case["leakage_group"])
        if case["id"].startswith("independent_") and case["id"].endswith(".05"):
            assert "loose level volume" in case["evidence"]["subject"]
            assert "20 C" in case["evidence"]["candidate_or_context"]
    assert all(len(groups) >= 3 for groups in paraphrase_groups.values())
    prune = service.CASES["independent_prune.01"]["evidence"]
    assert "uncooked dried" in prune["subject"] and "pitted" in prune["candidate_or_context"]
    assert "80% milk fat" in service.CASES["prepared_product.06"]["evidence"]["subject"]
    assert "70% dark chocolate" != service.CASES["percentages.02"]["evidence"]["subject"]
    transfers = [case for case in service.CASES.values() if case["task"] in {"nutrition_transfer", "density_transfer"}]
    assert len(transfers) == 206 and sum(case["split"] == "train" for case in transfers) == 140


def test_explicit_upgrade_preserves_credentials_and_confirms_no_human_labels(client, admin):
    configure(client, admin)
    old_unreviewed_draft()
    with SessionLocal() as db:
        encrypted_before = db.get(ConnectorConfig, "classifier").ciphertext
    compatible = client.get(BASE + "/corpus/compatibility", headers=admin).json()
    assert compatible["eligible"] and compatible["upgrade_required"]
    assert client.get(BASE + "/corpus", headers=admin).status_code == 409
    with SessionLocal() as db:
        assert db.get(ClassifierBenchmark, service._marker_id(service.CORPUS_HASH)) is None
    for payload in (
        {"previous_corpus_hash": "a" * 64, "confirm": True},
        {"previous_corpus_hash": PREVIOUS_CORPUS_HASH, "confirm": 1},
    ):
        assert client.post(BASE + "/corpus/upgrade", headers=admin, json=payload).status_code == 409
    payload = {"previous_corpus_hash": PREVIOUS_CORPUS_HASH, "confirm": True}
    revised = client.post(BASE + "/corpus/upgrade", headers=admin, json=payload)
    assert revised.status_code == 200, revised.text
    assert revised.json()["reviewed_count"] == 0 and not revised.json()["frozen"] and revised.json()["policy"] is None
    assert revised.json()["version"] == "synthetic-draft.v2"
    assert all(not case["reviewed"] for case in revised.json()["cases"])
    assert client.post(BASE + "/corpus/upgrade", headers=admin, json=payload).status_code == 200
    with SessionLocal() as db:
        assert db.get(ConnectorConfig, "classifier").ciphertext == encrypted_before
        receipts = db.scalars(select(ClassifierBenchmark).where(ClassifierBenchmark.kind == "draft_upgrade")).all()
        assert len(receipts) == 1 and set(receipts[0].document) == {
            "previous_corpus_hash",
            "corpus_hash",
            "version",
            "at",
        }
        assert db.get(ClassifierBenchmark, service._marker_id(PREVIOUS_CORPUS_HASH)).document["exposed"] is False


@pytest.mark.parametrize(
    "protection",
    ["reviewed", "frozen", "policy", "exposed", "history", "run", "marker_exposed", "marker_missing", "unknown_source"],
)
def test_upgrade_rejects_review_or_existing_evidence(client, admin, protection):
    old_unreviewed_draft()
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        doc = copy.deepcopy(row.document)
        if protection == "reviewed":
            doc["reviewed"] = {next(iter(service.CASES)): {"actor_id": "SIMULATED_TEST_ONLY"}}
        if protection == "frozen":
            doc["frozen"] = True
        if protection == "policy":
            doc["policy"] = {"version": "fixture"}
        if protection == "exposed":
            doc["holdout_exposed"] = True
        if protection == "unknown_source":
            doc["corpus_hash"] = "f" * 64
        repository.change(db, row, doc)
        if protection in {"history", "run"}:
            repository.create(db, "review_history" if protection == "history" else "run", {"status": "completed"})
        if protection in {"marker_exposed", "marker_missing"}:
            marker = repository.read(db, service._marker_id(PREVIOUS_CORPUS_HASH))
            if protection == "marker_exposed":
                repository.change(db, marker, {"corpus_hash": PREVIOUS_CORPUS_HASH, "exposed": True})
            else:
                db.delete(marker)
                db.commit()
    compatible = client.get(BASE + "/corpus/compatibility", headers=admin).json()
    assert compatible["upgrade_required"] and not compatible["eligible"]
    assert (
        client.post(
            BASE + "/corpus/upgrade",
            headers=admin,
            json={"previous_corpus_hash": PREVIOUS_CORPUS_HASH, "confirm": True},
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.get(ClassifierBenchmark, repository.REVIEW_ID).document == doc


def test_upgrade_rechecks_state_after_compatibility_read(client, admin):
    old_unreviewed_draft()
    assert client.get(BASE + "/corpus/compatibility", headers=admin).json()["eligible"]
    with SessionLocal() as other_db:
        row = repository.read(other_db, repository.REVIEW_ID)
        doc = copy.deepcopy(row.document)
        doc["reviewed"] = {next(iter(service.CASES)): {"actor_id": "SIMULATED_CONCURRENT_REVIEW"}}
        repository.change(other_db, row, doc)
    assert (
        client.post(
            BASE + "/corpus/upgrade",
            headers=admin,
            json={"previous_corpus_hash": PREVIOUS_CORPUS_HASH, "confirm": True},
        ).status_code
        == 409
    )


def test_historical_results_do_not_reinterpret_current_proposals(client, admin):
    created = run(client, admin).json()
    with SessionLocal() as db:
        row = repository.read(db, UUID(created["id"]))
        doc = copy.deepcopy(row.document)
        doc.update(
            corpus_hash=PREVIOUS_CORPUS_HASH,
            status="completed",
            completed=1,
            total=1,
            policy={"tasks": {"density_transfer": POLICY}},
            case_ids=["old_fixture_id_not_in_v2"],
        )
        doc["versions"]["corpus"] = "synthetic-draft.v1"
        doc["outcomes"] = [
            {
                "case_id": "old_fixture_id_not_in_v2",
                "task": "density_transfer",
                "group": "old_group",
                "expected": "eligible",
                "prediction": "eligible",
                "critical_negative": False,
                "status": "ok",
                "latency_ms": 3,
                "usage": {},
                "valid_response": True,
            }
        ]
        repository.change(db, row, doc)
    historical = client.get(BASE + f"/runs/{created['id']}", headers=admin).json()
    assert historical["summary"]["by_task"]["density_transfer"]["correct"] == 1
    assert historical["versions"]["corpus"] == "synthetic-draft.v1"
    gate = historical["summary"]["rollout"]["density_transfer"]
    assert gate["numeric_checks_passed"] is None and gate["checks"] == {} and not gate["eligible"]


def test_saved_transfer_task_policy_has_exact_bounded_run_scope(client, admin, monkeypatch):
    corpus = prepared(client, admin, monkeypatch)
    selected = {task: {**POLICY, "confidence": 0.97} for task in ("nutrition_transfer", "density_transfer")}
    response = client.post(
        BASE + "/policy", headers=admin, json={"review_hash": corpus["review_hash"], "tasks": selected}
    )
    assert response.status_code == 200
    assert response.json()["policy"]["tasks"]["nutrition_transfer"]["confidence"] == 0.97
    training = run(client, admin, split="train").json()
    heldout = run(client, admin, split="holdout").json()
    assert training["total"] == 140 and heldout["total"] == 66
    assert training["policy_hash"] == heldout["policy_hash"] == response.json()["policy"]["hash"]
