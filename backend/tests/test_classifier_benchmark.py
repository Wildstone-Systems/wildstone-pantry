"""Synthetic contract fixtures only. Test actors are NOT actual human review evidence."""

import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import httpx
import pytest

from app.db.session import SessionLocal
from modules.automation import benchmark_repository as repository
from modules.automation import benchmark_service as service
from modules.automation.benchmark_corpus import LABELS, request_for
from modules.automation.benchmark_metrics import summarize
from modules.automation.providers import clef

BASE = "/api/ai/classifier/benchmark"
POLICY = dict(
    confidence=0.8,
    margin=0.2,
    min_support=20,
    min_groups=5,
    max_false_positives=0,
    max_p95_ms=8000,
    max_failure_rate=0.1,
    budget_unit="requests",
    max_requests=500,
)


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "benchmark@example.com", "password": "BenchmarkPass987!"})
    r = client.post("/api/auth/login", json={"email": "benchmark@example.com", "password": "BenchmarkPass987!"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def prepared(client, admin, monkeypatch):
    """Seed simulated review state in the disposable test DB, never actual evidence."""
    client.get(BASE + "/corpus", headers=admin)
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        doc = copy.deepcopy(row.document)
        doc["reviewed"] = {key: {"actor_id": "TEST_ONLY_SIMULATED_REVIEW", "at": "fixture"} for key in service.CASES}
        repository.change(db, row, doc)
    corpus = client.get(BASE + "/corpus", headers=admin).json()
    r = client.post(
        BASE + "/corpus/freeze", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
    )
    assert r.status_code == 200, r.text
    r = client.post(
        BASE + "/policy",
        headers=admin,
        json={"review_hash": corpus["review_hash"], "tasks": {key: POLICY for key in LABELS}},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(service, "TRANSPORT_FIXTURE", True)
    return r.json()


def run(client, admin, **overrides):
    return client.post(
        BASE + "/runs",
        headers=admin,
        json={
            "engine": "rules",
            "split": "train",
            "max_requests": 500,
            "max_seconds": 1800,
            "budget_confirmed": True,
            **overrides,
        },
    )


def configure(client, admin):
    assert (
        client.put(
            "/api/ai/classifier/config",
            headers=admin,
            json={"enabled": True, "cloud_opt_in": True, "api_key": "synthetic-benchmark-key"},
        ).status_code
        == 200
    )


def diagnostic_prepared(client, admin, monkeypatch, selected_reviews=0):
    """Disposable test actors only; NOT actual human evidence."""
    configure(client, admin)
    corpus = client.get(BASE + "/corpus", headers=admin).json()
    culinary = [case for case in corpus["cases"] if case["task"] == "culinary_identity"][:14]
    culinary += [case for case in corpus["cases"] if case["task"] == "nutrition_transfer"][:selected_reviews]
    corrected_id = culinary[0]["id"]
    for case in culinary:
        response = client.post(
            BASE + "/corpus/review",
            headers=admin,
            json={
                "corpus_hash": corpus["corpus_hash"],
                "review_hash": corpus["review_hash"],
                "case_id": case["id"],
                "label": "substitution" if case["id"] == corrected_id else case["label"],
                "confirm": True,
            },
        )
        assert response.status_code == 200, response.text
        corpus = response.json()
    response = client.post(
        BASE + "/corpus/diagnostic-freeze", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
    )
    assert response.status_code == 200, response.text
    monkeypatch.setattr(service, "TRANSPORT_FIXTURE", True)
    return response.json()


def diagnostic_policy(client, admin, corpus):
    response = client.post(
        BASE + "/policy",
        headers=admin,
        json={
            "review_hash": corpus["review_hash"],
            "tasks": {task: corpus["diagnostic_policy"] for task in service.DIAGNOSTIC_TASKS},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_diagnostic_preserves_reviews_strict_freeze_and_reference_provenance(client, admin, monkeypatch):
    corpus = diagnostic_prepared(client, admin, monkeypatch)
    assert corpus["reviewed_count"] == 14
    assert sum(case["reviewed"] for case in corpus["cases"]) == 14
    assert next(case for case in corpus["cases"] if case["reviewed"])["label"] == "substitution"
    assert corpus["freeze_kind"] == "owner_authorized_diagnostic"
    assert corpus["diagnostic"]["selected_reviewed_count"] == 0
    assert corpus["diagnostic"]["selected_count"] == 206
    assert corpus["diagnostic"]["split_counts"] == {"train": 140, "holdout": 66}
    assert (
        client.post(
            BASE + "/corpus/freeze", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
        ).status_code
        == 409
    )
    diagnostic_policy(client, admin, corpus)
    response = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 140 and body["human_reviewed"] is False
    assert body["selected_reviewed_count"] == 0
    assert set(body["reference_sources"].values()) == {"ai_proposed"}
    assert body["label_provenance"] == "ai_proposed_reference"
    assert "not human-ground-truth accuracy" in body["metric_interpretation"]
    assert all(value["eligible"] is False for value in body["summary"]["rollout"].values())
    with SessionLocal() as db:
        stored = repository.read(db, repository.REVIEW_ID).document
        assert len(stored["reviewed"]) == 14 and "freeze_receipt" not in stored
        assert len(stored["diagnostic"]["sources"]) == 206
        marker = service._marker(db).document
        assert set(marker["diagnostic"]) == {"authorization_hash", "split_used"}
        assert "actor" not in json.dumps(marker)


def test_diagnostic_fixed_policy_and_budgets_no_retuning(client, admin, monkeypatch):
    corpus = diagnostic_prepared(client, admin, monkeypatch)
    custom = {task: copy.deepcopy(corpus["diagnostic_policy"]) for task in service.DIAGNOSTIC_TASKS}
    custom["nutrition_transfer"]["confidence"] = 0.97
    assert (
        client.post(
            BASE + "/policy", headers=admin, json={"review_hash": corpus["review_hash"], "tasks": custom}
        ).status_code
        == 409
    )
    diagnostic_policy(client, admin, corpus)
    assert (
        client.post(
            BASE + "/policy",
            headers=admin,
            json={
                "review_hash": corpus["review_hash"],
                "tasks": {task: corpus["diagnostic_policy"] for task in service.DIAGNOSTIC_TASKS},
            },
        ).status_code
        == 409
    )
    for overrides in ({"engine": "rules"}, {"max_requests": 141}, {"max_seconds": 1201}, {"budget_confirmed": False}):
        args = dict(engine="classifier", max_requests=140, max_seconds=1200)
        args.update(overrides)
        assert run(client, admin, **args).status_code == 409
    assert client.get(BASE + "/corpus", headers=admin).json()["diagnostic"]["split_used"] == {
        "train": False,
        "holdout": False,
    }


def test_diagnostic_allocation_survives_cancel_delete_reopen_expiry(client, admin, monkeypatch):
    corpus = diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch))
    created = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).json()
    assert client.post(BASE + f"/runs/{created['id']}/cancel", headers=admin).status_code == 200
    assert client.delete(BASE + f"/runs/{created['id']}", headers=admin).status_code == 200
    assert run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).status_code == 409
    assert (
        client.post(
            BASE + "/corpus/reopen", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    current = client.get(BASE + "/corpus", headers=admin).json()
    assert not current["frozen"]
    assert (
        client.post(
            BASE + "/corpus/diagnostic-freeze",
            headers=admin,
            json={"review_hash": current["review_hash"], "confirm": True},
        ).status_code
        == 409
    )
    assert run(client, admin).status_code == 409
    with SessionLocal() as db:
        assert service._marker(db).document["diagnostic"]["split_used"]["train"] is True


def test_diagnostic_config_binding_and_strict_confirmation(client, admin, monkeypatch):
    corpus = diagnostic_prepared(client, admin, monkeypatch)
    assert (
        client.post(
            BASE + "/corpus/diagnostic-freeze", headers=admin, json={"review_hash": "0" * 64, "confirm": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            BASE + "/corpus/diagnostic-freeze", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": 1}
        ).status_code
        == 409
    )
    assert (
        client.put("/api/ai/classifier/config", headers=admin, json={"api_key": "changed-synthetic-key"}).status_code
        == 200
    )
    assert (
        client.post(
            BASE + "/policy",
            headers=admin,
            json={
                "review_hash": corpus["review_hash"],
                "tasks": {task: corpus["diagnostic_policy"] for task in service.DIAGNOSTIC_TASKS},
            },
        ).status_code
        == 409
    )


def test_diagnostic_step_provenance_and_revoked_kind_zero_io(client, admin, monkeypatch):
    diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch))
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).json()
    response = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == 1 and result["human_reviewed"] is False
    assert next(iter(result["summary"]["by_task"].values()))["reference_basis"] == "ai_proposed_reference"
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        doc = copy.deepcopy(row.document)
        doc["freeze_kind"] = "human_reviewed"
        repository.change(db, row, doc)
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 409
    assert len(calls) == 1


def test_diagnostic_two_session_claim_and_retention_cap_rollback(client, admin, monkeypatch):
    corpus = diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch))
    with SessionLocal() as db:
        for _ in range(20):
            repository.create(db, "run", {"status": "completed"})
    assert run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).status_code == 409
    assert client.get(BASE + "/corpus", headers=admin).json()["diagnostic"]["split_used"]["train"] is False
    with SessionLocal() as db:
        repository.remove(db, repository.recent(db)[0].id)
    with SessionLocal() as first, SessionLocal() as second:
        revision1, revision2 = service._review(first).revision, service._review(second).revision
        claim = (service._marker_id(service.CORPUS_HASH), corpus["diagnostic"]["authorization_hash"], "train")
        repository.create(first, "run", {"status": "completed"}, diagnostic_claim=(*claim, revision1))
        with pytest.raises(ValueError, match="already allocated"):
            repository.create(second, "run", {"status": "completed"}, diagnostic_claim=(*claim, revision2))
        second.rollback()
        assert service._marker(second).document["diagnostic"]["split_used"]["train"] is True


def test_diagnostic_mixed_dynamic_sources_and_holdout_marker_preserved(client, admin, monkeypatch):
    corpus = diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch, selected_reviews=3))
    assert corpus["reviewed_count"] == 17
    assert corpus["diagnostic"]["selected_reviewed_count"] == 3
    assert corpus["diagnostic"]["label_provenance"] == "mixed_reference"
    transport(monkeypatch)
    train = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).json()
    assert train["selected_reviewed_count"] == 3 and not train["human_reviewed"]
    held = run(client, admin, engine="classifier", split="holdout", max_requests=66, max_seconds=600).json()
    response = client.post(BASE + f"/runs/{held['id']}/step", headers=admin)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        marker = service._marker(db).document
        assert marker["exposed"] is True
        assert marker["diagnostic"]["split_used"] == {"train": True, "holdout": True}
        row = repository.read(db, repository.REVIEW_ID)
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    current = client.get(BASE + "/corpus", headers=admin).json()
    assert current["holdout_exposed"] is True
    assert (
        client.post(
            BASE + "/corpus/diagnostic-freeze",
            headers=admin,
            json={"review_hash": current["review_hash"], "confirm": True},
        ).status_code
        == 409
    )


def test_diagnostic_freeze_stale_cas_preserves_concurrent_review(client, admin):
    client.get(BASE + "/corpus", headers=admin)
    with SessionLocal() as first, SessionLocal() as second:
        stale = service._review(first)
        draft = copy.deepcopy(stale.document)
        marker_id = service._marker(first).id
        current = service._review(second)
        corrected = copy.deepcopy(current.document)
        key = next(iter(service.CASES))
        corrected["labels"][key] = "substitution"
        corrected["reviewed"][key] = {"actor_id": "TEST_ONLY", "at": "fixture"}
        repository.change(second, current, corrected)
        with pytest.raises(ValueError, match="evidence changed"):
            repository.diagnostic_freeze(first, stale, draft, marker_id, "0" * 64)
        first.rollback()
        result = service._review(first).document
        assert result["labels"][key] == "substitution"
        assert result["reviewed"][key] == corrected["reviewed"][key]
        assert "diagnostic" not in service._marker(first).document


@pytest.mark.parametrize("change", ["credentials", "source"])
def test_diagnostic_run_keeps_authorized_snapshot_across_admission_barrier(client, admin, monkeypatch, change):
    """Deterministic barrier AFTER validation, BEFORE document assembly.

    Credential rotation commits in a real second SQLite session. Source change
    uses the fingerprint seam; no real files, credentials or provider are used.
    """
    diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch))
    calls, _ = transport(monkeypatch)
    with SessionLocal() as db:
        snapshot = copy.deepcopy(service._review(db).document["diagnostic"])
    validate = service._validate_diagnostic

    def admission_barrier(doc, db):
        validate(doc, db)
        if change == "credentials":
            with SessionLocal() as other:
                service.classifier_service.update_config(other, {"api_key": "rotated-synthetic-key"})
        else:
            monkeypatch.setattr(service, "content_fingerprint", lambda: "f" * 64)

    monkeypatch.setattr(service, "_validate_diagnostic", admission_barrier)
    response = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200)
    assert response.status_code == 200, response.text
    created = response.json()
    with SessionLocal() as db:
        stored = copy.deepcopy(repository.read(db, UUID(created["id"])).document)
        receipt = service._review(db).document
        assert stored["config_revision"] == snapshot["config_revision"]
        assert stored["versions"]["content_hash"] == snapshot["content_hash"]
        assert stored["versions"]["source_sha"] == snapshot["source_sha"]
        assert {key: stored["versions"][key] for key in snapshot["target"]} == snapshot["target"]
        assert service._receipt_matches(receipt, stored)
        # Rebinding any protected field to newly observed settings/source is not
        # an authorization, even if the current configured target accepts it.
        for field in ("config_revision", "content_hash", "target"):
            altered = copy.deepcopy(stored)
            if field == "config_revision":
                altered[field] = "unapproved"
            elif field == "content_hash":
                altered["versions"][field] = "unapproved"
            else:
                altered["versions"]["model"] = "unapproved"
            assert not service._receipt_matches(receipt, altered)
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 409
    assert calls == []
    current = client.get(BASE + f"/runs/{created['id']}", headers=admin).json()
    assert current["budget"]["attempted"] == 0 and current["completed"] == 0


def test_diagnostic_below_threshold_retains_only_validated_audit_scalars(client, admin, monkeypatch):
    diagnostic_policy(client, admin, diagnostic_prepared(client, admin, monkeypatch))

    def handler(request):
        choices = list(json.loads(request.content)["questions"]["q_1"]["criteria"])
        return httpx.Response(
            200,
            json={
                "model": "clef",
                "answers": {
                    "q_1": {
                        "type": "choice",
                        "choice": choices[0],
                        "confidence": 0.7,
                        "probabilities": dict(zip(choices, [0.7, 0.2, 0.1], strict=True)),
                    }
                },
            },
        )

    monkeypatch.setattr(clef, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    created = run(client, admin, engine="classifier", max_requests=140, max_seconds=1200).json()
    response = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert response.status_code == 200, response.text
    result = response.json()
    outcome = result["outcomes"][0]
    assert outcome["status"] == "abstain" and outcome["prediction"] is None
    assert outcome["raw_prediction"] == "eligible"
    assert outcome["runner_up_probability"] == 0.2
    assert outcome["decision_margin"] == pytest.approx(0.5)
    metric = result["summary"]["by_task"][outcome["task"]]
    assert metric["abstained"] == 1
    assert metric["correct"] == metric["coverage"] == metric["false_positives"] == 0
    assert not {"raw_payload", "rationale", "api_key"} & outcome.keys()


def transport(monkeypatch, *, slow=False):
    calls, closed = [], []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await anyio.sleep(0.02)

        async def aclose(self):
            closed.append(True)

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        choices = list(payload["questions"]["q_1"]["criteria"])
        if slow:
            return httpx.Response(200, stream=Body())
        return httpx.Response(
            200,
            json={
                "model": "clef",
                "answers": {
                    "q_1": {
                        "type": "choice",
                        "choice": choices[0],
                        "probabilities": {key: 1.0 if i == 0 else 0.0 for i, key in enumerate(choices)},
                        "confidence": 1.0,
                    }
                },
            },
        )

    monkeypatch.setattr(clef, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls, closed


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/corpus"),
        ("post", "/corpus/review"),
        ("post", "/corpus/freeze"),
        ("post", "/corpus/diagnostic-freeze"),
        ("post", "/policy"),
        ("post", "/runs"),
        ("get", "/runs"),
    ],
)
def test_admin_required_even_kiosk(client, method, path):
    r = getattr(client, method)(BASE + path, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 401


def test_corpus_draft_rubrics_and_connected_split_no_target_in_request(client, admin):
    corpus = client.get(BASE + "/corpus", headers=admin).json()
    assert corpus["case_count"] >= 200 and corpus["reviewed_count"] == 0 and not corpus["frozen"]
    assert len({case["edge_family"] for case in corpus["cases"]}) == 13
    memberships = {}
    for case in corpus["cases"]:
        assert case["provenance"] == "ai_proposed"
        for member in case["family_members"]:
            assert member not in memberships or memberships[member] == case["split"]
            memberships[member] = case["split"]
        payload = request_for(case)
        assert json.loads(payload["state"]) == case["evidence"]
        assert "expected" not in payload and "proposed_label" not in payload
    assert "imperative not ingredient" not in request_for(service.CASES["recipe_contamination.01"])["state"]
    assert "section heading" not in request_for(service.CASES["recipe_contamination.05"])["state"]
    assert "nutrition_transfer" in corpus["rubrics"]
    for task in ("nutrition_transfer", "density_transfer"):
        assert len({c["leakage_group"] for c in corpus["cases"] if c["task"] == task and c["split"] == "holdout"}) >= 5


def test_single_review_hash_guard_strict_confirmation_and_freeze_gate(client, admin):
    corpus = client.get(BASE + "/corpus", headers=admin).json()
    payload = {
        "corpus_hash": corpus["corpus_hash"],
        "review_hash": corpus["review_hash"],
        "case_id": corpus["cases"][0]["id"],
        "label": "insufficient",
        "confirm": True,
    }
    for confirmation in (1, "true", False):
        assert (
            client.post(BASE + "/corpus/review", headers=admin, json={**payload, "confirm": confirmation}).status_code
            == 409
        )
    reviewed = client.post(BASE + "/corpus/review", headers=admin, json=payload).json()
    assert reviewed["reviewed_count"] == 1 and reviewed["review_hash"] != corpus["review_hash"]
    assert client.post(BASE + "/corpus/review", headers=admin, json=payload).status_code == 409
    assert (
        client.post(
            BASE + "/corpus/freeze", headers=admin, json={"review_hash": reviewed["review_hash"], "confirm": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            BASE + "/policy",
            headers=admin,
            json={"review_hash": reviewed["review_hash"], "tasks": {"culinary_identity": POLICY}},
        ).status_code
        == 409
    )
    assert run(client, admin, split="holdout").status_code == 409
    assert run(client, admin, engine="classifier").status_code == 409
    assert run(client, admin, budget_confirmed=False).status_code == 409
    assert run(client, admin, evidence_kind="live_provider").status_code == 409


def test_rules_real_public_projection_and_budget(client, admin):
    created = run(client, admin, max_requests=1).json()
    r = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["completed"] == 1 and result["budget"]["attempted"] == 1
    assert result["outcomes"][0]["reason"] in {"canonical_key_equal", "canonical_no_exact_match"}
    exhausted = client.post(BASE + f"/runs/{created['id']}/step", headers=admin).json()
    assert exhausted["status"] == "interrupted" and exhausted["completed"] == 1
    assert result["versions"]["content_hash"] and all(
        not gate["eligible"] for gate in result["summary"]["rollout"].values()
    )


def test_metrics_known_examples_partial_cost_and_more_than_cache_cap():
    rows = [
        {
            "case_id": str(i),
            "task": "culinary_identity",
            "group": f"g{i%10}",
            "expected": "different",
            "critical_negative": True,
            "status": "ok",
            "prediction": "same",
            "latency_ms": i + 1,
            "probability": 0.9,
            "usage": {"cost_usd": 0.01, "input_tokens": 2, "output_tokens": 1},
        }
        for i in range(201)
    ]
    rows[0].update(status="abstain", prediction=None, usage={})
    metric = summarize(rows)["culinary_identity"]
    assert metric["support"] == 201 and metric["false_positives"] == 200 and metric["critical_false_positives"] == 200
    assert metric["p50_ms"] == 101 and metric["p95_ms"] == 191
    assert metric["abstained"] == 1 and metric["coverage"] == 200 / 201
    assert metric["total_cost_usd"] is None and metric["unknown_cost_count"] == 1
    assert metric["input_tokens"] is None and metric["review_corrections_saved"] is None
    for row in rows:
        row["usage"] = {"cost_usd": 1e308, "input_tokens": 9007199254740991}
    overflow = summarize(rows)["culinary_identity"]
    assert overflow["known_cost_usd"] is None and overflow["total_cost_usd"] is None
    assert overflow["input_tokens"] is None


def test_fixture_run_aggregates_independently_and_never_admits(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier", split="holdout", max_requests=1).json()
    assert created["evidence_kind"] == "contract_fixture"
    result = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert result.status_code == 200, result.text
    result = result.json()
    assert len(calls) == 1 and result["completed"] == 1
    assert result["summary"]["by_task"] and result["outcomes"][0]["usage"] == {}
    assert all(not gate["eligible"] for gate in result["summary"]["rollout"].values())
    assert "synthetic-benchmark-key" not in json.dumps(result)
    with SessionLocal() as db:
        assert repository.read(db, UUID(created["id"])).document["outcomes"]
    assert client.get("/api/ai/classifier/evaluations/" + created["id"], headers=admin).status_code == 404
    # A correction can be reviewed, but exposed holdout cannot be used for retuning.
    corpus = client.get(BASE + "/corpus", headers=admin).json()
    reopened = client.post(
        BASE + "/corpus/reopen", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
    )
    assert reopened.status_code == 200 and reopened.json()["holdout_exposed"] and not reopened.json()["frozen"]
    assert reopened.json()["reviewed_count"] == 0 and reopened.json()["policy"] is None


def test_cancel_deadline_closes_stream_no_followup(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, closed = transport(monkeypatch, slow=True)
    created = run(client, admin, engine="classifier", max_seconds=1).json()
    result = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert result.status_code == 200, result.text
    assert result.json()["outcomes"][0]["reason"] == "timeout"
    assert len(calls) == 1 and closed == [True]
    cancelled = client.post(BASE + f"/runs/{created['id']}/cancel", headers=admin).json()
    assert cancelled["status"] == "cancelled"
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 409
    assert len(calls) == 1


def test_disconnection_closes_stream_and_marks_cancelled(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, closed = transport(monkeypatch, slow=True)
    created = run(client, admin, engine="classifier").json()

    async def disconnected():
        return True

    async def execute():
        with SessionLocal() as db:
            return await service.step(db, UUID(created["id"]), disconnected)

    result = anyio.run(execute)
    assert result["status"] == "cancelled" and closed == [True] and len(calls) == 1


def test_stale_restart_and_disabled_config_do_not_call(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier").json()
    client.put("/api/ai/classifier/config", headers=admin, json={"enabled": False})
    result = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert result.status_code == 409 and calls == []
    with SessionLocal() as db:
        row = repository.read(db, UUID(created["id"]))
        doc = copy.deepcopy(row.document)
        doc.update(status="running", lease={"id": "fixture", "at": 0})
        repository.change(db, row, doc)
    assert client.get(BASE + f"/runs/{created['id']}", headers=admin).json()["status"] == "interrupted"
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 409


def test_retention_cap_and_expiry(client, admin):
    for _ in range(20):
        assert run(client, admin).status_code == 200
    assert run(client, admin).status_code == 409
    runs = client.get(BASE + "/runs", headers=admin).json()["runs"]
    key = UUID(runs[0]["id"])
    with SessionLocal() as db:
        row = repository.read(db, key)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(BASE + f"/runs/{key}", headers=admin).status_code == 409
    assert run(client, admin).status_code == 200


def test_persisted_aggregate_survives_diagnostic_cache_clear(client, admin):
    from modules.automation import classifier_repository

    created = run(client, admin).json()
    with SessionLocal() as db:
        row = repository.read(db, UUID(created["id"]))
        doc = copy.deepcopy(row.document)
        doc["outcomes"] = [
            {
                "case_id": str(i),
                "task": "density_transfer",
                "group": f"food{i%12}",
                "expected": "unsuitable",
                "critical_negative": True,
                "status": "ok",
                "prediction": "unsuitable",
                "latency_ms": i,
                "usage": {},
            }
            for i in range(201)
        ]
        doc.update(completed=201, total=201, status="completed")
        repository.change(db, row, doc)
        classifier_repository.clear_results(db)
    result = client.get(BASE + f"/runs/{created['id']}", headers=admin).json()
    assert len(result["outcomes"]) == 201 and result["summary"]["by_task"]["density_transfer"]["support"] == 201
    assert result["summary"]["by_task"]["density_transfer"]["correct"] == 201


def test_duplicate_step_and_server_cancel_close_active_http(client, admin, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, closed = transport(monkeypatch, slow=True)
    entered = threading.Event()
    original = clef._client

    def client_factory():
        entered.set()
        return original()

    monkeypatch.setattr(clef, "_client", client_factory)
    created = run(client, admin, engine="classifier").json()
    path = BASE + f"/runs/{created['id']}"
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.post, path + "/step", headers=admin)
        assert entered.wait(2)
        assert client.post(path + "/step", headers=admin).status_code == 409
        cancelled = client.post(path + "/cancel", headers=admin)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        result = pending.result(timeout=3)
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    assert result.json()["budget"]["attempted"] == 1 and calls and len(calls) == 1 and closed == [True]


def test_no_truth_writes_and_unconfigured_legacy_no_key_reuse(client, admin, monkeypatch):
    from sqlalchemy import inspect, text

    from app.db.session import engine

    calls, _ = transport(monkeypatch)
    configure(client, admin)

    def counts():
        with engine.connect() as connection:
            return {
                table: connection.scalar(text(f'SELECT COUNT(*) FROM "{table}"'))
                for table in inspect(connection).get_table_names()
                if table not in {"classifier_benchmarks", "classifier_evaluations"}
            }

    before = counts()
    created = run(client, admin, engine="legacy_judge").json()
    result = client.post(BASE + f"/runs/{created['id']}/step", headers=admin).json()
    assert result["outcomes"][0]["status"] == "unavailable"
    assert result["outcomes"][0]["reason"] == "legacy_judge_unconfigured" and calls == []
    assert counts() == before


def test_benchmark_migration_roundtrip_preserves_food():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = importlib.import_module("migrations.versions.0060_classifier_benchmarks")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_food (name TEXT)"))
        connection.execute(text("INSERT INTO existing_food VALUES ('Apple')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "classifier_benchmarks" in inspect(connection).get_table_names()
            migration.downgrade()
            assert "classifier_benchmarks" not in inspect(connection).get_table_names()
            migration.upgrade()
        assert connection.scalar(text("SELECT name FROM existing_food")) == "Apple"


def test_holdout_exposure_refresh_never_overwrites_another_reservation(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier", split="holdout").json()
    key = UUID(created["id"])
    original = repository.change

    def racing_change(db, row, document, **kwargs):
        is_exposure = row.kind == "review" and document.get("holdout_exposed")
        original(db, row, document, **kwargs)
        if is_exposure:
            # Real second DB transaction reserves while first is between its
            # holdout exposure commit and run re-read. No ORM/DB mock.
            with SessionLocal() as concurrent_db:
                other = repository.read(concurrent_db, key)
                reserved = copy.deepcopy(other.document)
                reserved.update(status="running", lease={"id": "other-request", "at": __import__("time").time()})
                reserved["budget"]["attempted"] = 1
                original(concurrent_db, other, reserved)

    monkeypatch.setattr(repository, "change", racing_change)
    response = client.post(BASE + f"/runs/{key}/step", headers=admin)
    assert response.status_code == 409 and calls == []
    with SessionLocal() as db:
        stored = repository.read(db, key).document
        assert stored["lease"]["id"] == "other-request" and stored["budget"]["attempted"] == 1


def test_training_policy_revision_allowed_but_holdout_blocks_retuning(client, admin, monkeypatch):
    corpus = prepared(client, admin, monkeypatch)
    changed = client.post(
        BASE + "/policy",
        headers=admin,
        json={"review_hash": corpus["review_hash"], "tasks": {"culinary_identity": {**POLICY, "confidence": 0.9}}},
    )
    assert changed.status_code == 200 and changed.json()["policy"]["hash"] != corpus["policy"]["hash"]
    created = run(client, admin, split="holdout", max_requests=1).json()
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 200
    assert (
        client.post(
            BASE + "/policy",
            headers=admin,
            json={"review_hash": corpus["review_hash"], "tasks": {"culinary_identity": POLICY}},
        ).status_code
        == 409
    )


def test_same_provider_secret_rotation_invalidates_budget(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier").json()
    client.put("/api/ai/classifier/config", headers=admin, json={"api_key": "different-synthetic-key"})
    result = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert result.status_code == 409 and calls == []


def test_stale_delete_cannot_erase_new_running_reservation(client, admin):
    created = run(client, admin).json()
    key = UUID(created["id"])
    with SessionLocal() as stale_db:
        stale = repository.read(stale_db, key)
        assert stale.document["status"] == "ready"
        with SessionLocal() as other_db:
            row = repository.read(other_db, key)
            doc = copy.deepcopy(row.document)
            doc.update(status="running", lease={"id": "reserved", "at": __import__("time").time()})
            doc["budget"]["attempted"] = 1
            repository.change(other_db, row, doc)
        with pytest.raises(ValueError):
            repository.remove(stale_db, key)
    with SessionLocal() as db:
        assert repository.read(db, key).document["budget"]["attempted"] == 1


def test_reopened_review_revokes_pending_provider_run(client, admin, monkeypatch):
    corpus = prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier").json()
    assert (
        client.post(
            BASE + "/corpus/reopen", headers=admin, json={"review_hash": corpus["review_hash"], "confirm": True}
        ).status_code
        == 200
    )
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 409
    assert calls == []


def test_rotation_after_reservation_never_dispatches_new_credentials(client, admin, monkeypatch):
    from modules.shopping.services import connector_config

    prepared(client, admin, monkeypatch)
    configure(client, admin)
    calls, _ = transport(monkeypatch)
    created = run(client, admin, engine="classifier").json()
    original = repository.change

    def rotate_after_reservation(db, row, document, **kwargs):
        reserved = row.kind == "run" and document["status"] == "running"
        original(db, row, document, **kwargs)
        if reserved:
            with SessionLocal() as other_db:
                connector_config.set_classifier_config(other_db, {"api_key": "new-unapproved-synthetic-key"})

    monkeypatch.setattr(repository, "change", rotate_after_reservation)
    response = client.post(BASE + f"/runs/{created['id']}/step", headers=admin)
    assert response.status_code == 200 and calls == []
    assert response.json()["budget"]["attempted"] == 1
    assert response.json()["outcomes"][0]["network_attempted"] is False


def test_review_expiry_cannot_erase_holdout_exposure(client, admin, monkeypatch):
    prepared(client, admin, monkeypatch)
    created = run(client, admin, split="holdout", max_requests=1).json()
    assert client.post(BASE + f"/runs/{created['id']}/step", headers=admin).status_code == 200
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    fresh = client.get(BASE + "/corpus", headers=admin).json()
    assert fresh["holdout_exposed"] and fresh["reviewed_count"] == 0
    # Even simulated fresh human confirmations cannot make this same exposed
    # corpus eligible for retuning after its private reviewer history expires.
    with SessionLocal() as db:
        row = repository.read(db, repository.REVIEW_ID)
        doc = copy.deepcopy(row.document)
        doc["reviewed"] = {key: {"actor_id": "TEST_ONLY", "at": "fixture"} for key in service.CASES}
        doc["frozen"] = True
        doc["frozen_hash"] = service.review_hash(doc)
        repository.change(db, row, doc)
    current = client.get(BASE + "/corpus", headers=admin).json()
    assert (
        client.post(
            BASE + "/policy",
            headers=admin,
            json={"review_hash": current["review_hash"], "tasks": {"culinary_identity": POLICY}},
        ).status_code
        == 409
    )
