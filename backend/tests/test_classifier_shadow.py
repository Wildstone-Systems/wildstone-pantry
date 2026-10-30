"""Observable classifier contracts with isolated SQLite and transport fixtures.

These tests do not measure a live Clef model's accuracy or availability.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import httpx
import pytest
from sqlalchemy import func, select

from app.core.crypto import decrypt_json, encrypt_json
from app.db.session import SessionLocal
from app.models import ConnectorConfig
from modules.automation import classifier_repository
from modules.automation.classifier_contract import ClassifierConfig, EvaluationRequest
from modules.automation.models import ClassifierEvaluation
from modules.automation.providers import clef
from modules.shopping.services import connector_config

BASE = "/api/ai/classifier"
SECRET = "synthetic-classifier-key-never-return"
REQUEST = {
    "state": "Private synthetic apple",
    "questions": {
        "q_1": {"type": "choice", "instructions": "Food or other?", "criteria": {"c_1": "Food", "c_2": "Other"}}
    },
}
REPLY = {
    "model": "clef",
    "answers": {
        "q_1": {"type": "choice", "choice": "c_1", "probabilities": {"c_1": 0.9, "c_2": 0.1}, "confidence": 0.53}
    },
    "usage": {"input_tokens": 10, "output_tokens": 0},
}


@pytest.fixture
def admin(client):
    client.post("/api/auth/bootstrap-admin", json={"email": "classifier@example.com", "password": "ClassifierPass987!"})
    response = client.post(
        "/api/auth/login", json={"email": "classifier@example.com", "password": "ClassifierPass987!"}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def transport(monkeypatch):
    calls = []

    def install(payload=None, code=200, error=None, headers=None):
        def handler(request):
            calls.append(request)
            if error:
                raise error
            return httpx.Response(code, json=REPLY if payload is None else payload, headers=headers)

        monkeypatch.setattr(
            clef,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False),
        )
        return calls

    install()
    return install, calls


def configure(client, admin, **overrides):
    response = client.put(
        BASE + "/config", headers=admin, json={"enabled": True, "cloud_opt_in": True, "api_key": SECRET, **overrides}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_default_disabled_separate_config_and_no_transport(client, admin, transport):
    _, calls = transport
    view = client.get(BASE + "/config", headers=admin).json()
    assert view["config"]["enabled"] is False
    assert view["api_key_set"] is False
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()
    assert result["status"] == "abstain" and result["reason_code"] == "disabled"
    assert calls == []
    assert client.get("/api/ai/status", headers=admin).json()["active_vision_provider"] is None


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/config"),
        ("put", "/config"),
        ("delete", "/config"),
        ("post", "/test"),
        ("post", "/evaluations"),
        ("get", "/evaluations/" + str(uuid4())),
    ],
)
def test_classifier_always_requires_authentication(client, method, path):
    response = getattr(client, method)(BASE + path, **({"json": {}} if method in {"put", "post"} else {}))
    assert response.status_code == 401


def test_encryption_partial_secret_restart_identity_rotation_and_disconnect(client, admin):
    configure(client, admin)
    with SessionLocal() as db:
        row = db.get(ConnectorConfig, "classifier")
        assert SECRET not in row.ciphertext
        assert decrypt_json(row.ciphertext)["api_key"] == SECRET
    response = client.put(BASE + "/config", headers=admin, json={"enabled": False, "api_key": ""})
    assert response.json()["api_key_set"] is True
    assert SECRET not in response.text
    # A fresh session/API read rehydrates persisted encrypted state.
    assert client.get(BASE + "/config", headers=admin).json()["api_key_set"] is True
    local = {"execution_location": "local", "endpoint": "http://127.0.0.1:11434", "model": "clef"}
    response = client.put(BASE + "/config", headers=admin, json=local)
    assert response.status_code == 200 and response.json()["api_key_set"] is False
    response = client.delete(BASE + "/config", headers=admin)
    assert response.json()["config"]["enabled"] is False
    with SessionLocal() as db:
        assert db.get(ConnectorConfig, "classifier") is None


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": SECRET, "enabled": "false"},
        {"api_key": SECRET, "endpoint": "https://evil.example"},
        {"api_key": SECRET, "unexpected": 1},
        [SECRET],
        {"api_key": SECRET, "enabled": None},
    ],
)
def test_invalid_config_never_echoes_secret_or_mutates(client, admin, body):
    response = client.put(BASE + "/config", headers=admin, json=body)
    assert response.status_code == 400 and SECRET not in response.text
    assert client.get(BASE + "/config", headers=admin).json()["api_key_set"] is False


def test_classifier_generic_connector_bypass_is_closed(client, admin):
    for method in ("get", "put", "delete", "post"):
        path = "/api/admin/integrations/classifier" + ("/test" if method == "post" else "")
        response = getattr(client, method)(
            path, headers=admin, **({"json": {"updates": {"enabled": "true"}}} if method == "put" else {})
        )
        assert response.status_code == 404
    with SessionLocal() as db, pytest.raises(LookupError):
        connector_config.set_config(db, "classifier", {"enabled": "true"})


@pytest.mark.parametrize(
    "location,endpoint,model",
    [
        ("local", "http://localhost:11434", "clef:cloud"),
        ("cloud_direct", "https://ollama.com.evil.example", "clef"),
        ("cloud_direct", "https://ollama.com@evil.example.com", "clef"),
        ("cloud_direct", "https://ollama.com/private", "clef"),
        ("cloud_direct", "https://ollama.com?key=secret", "clef"),
        ("cloud_direct", "http://ollama.com", "clef"),
        ("local", "http://169.254.169.254:11434", "clef"),
        ("local", "http://8.8.8.8:11434", "clef"),
        ("cloud_via_local", "http://localhost:11434", "clef"),
    ],
)
def test_endpoint_and_model_restrictions(client, admin, transport, location, endpoint, model):
    response = client.put(
        BASE + "/config", headers=admin, json={"execution_location": location, "endpoint": endpoint, "model": model}
    )
    assert response.status_code == 400
    assert transport[1] == []


@pytest.mark.parametrize("endpoint", [
    "http://localhost:11434", "http://127.0.0.1:80", "http://[::1]:11434",
    "http://192.168.1.50:11434", "http://10.20.30.40:18114", "https://172.16.10.5:8443",
    "http://[fd12:3456::20]:11434", "http://[::ffff:192.168.1.50]:11434",
    "http://ollama:11434", "http://host.docker.internal:11434", "https://ollama.home.arpa",
    "https://ollama.example.test:8443/",
])
def test_ollama_server_origins_support_local_lan_and_admin_dns_without_resolution(endpoint):
    for location, model in (("local", "clef"), ("cloud_via_local", "clef:cloud")):
        config = ClassifierConfig(execution_location=location, endpoint=endpoint, model=model)
        assert config.endpoint == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254:11434", "http://[fe80::1]:11434", "http://0.0.0.0:11434",
    "http://[::]:11434", "http://224.0.0.1:11434", "http://8.8.8.8:11434",
    "http://[::ffff:169.254.169.254]:11434", "http://0xa9.0xfe.0xa9.0xfe:11434",
    "http://2130706433:11434", "http://127.1:11434", "http://ollama:0",
    "ftp://ollama:11434", "http://ollama:11434/api", "http://user:secret@ollama:11434",
    "http://@ollama:11434", "http://ollama:11434?", "http://ollama:11434#",
    "http://bad_host:11434", "http://bad..host:11434", "http://ollama\n:11434",
])
def test_ollama_server_origins_still_reject_unsafe_or_malformed_addresses(endpoint):
    with pytest.raises(ValueError):
        ClassifierConfig(execution_location="local", endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://192.168.1.50:18114", "https://ollama.example.test:8443"])
def test_lan_config_roundtrip_and_explicit_local_request_do_not_forward_cloud_key(client, admin, transport, endpoint):
    configure(client, admin)
    response = client.put(BASE + "/config", headers=admin, json={
        "execution_location": "local", "endpoint": endpoint, "model": "clef", "cloud_opt_in": False,
    })
    assert response.status_code == 200, response.text
    assert response.json()["api_key_set"] is False
    saved = client.get(BASE + "/config", headers=admin).json()
    assert saved["config"]["endpoint"] == endpoint and saved["configured"] is True
    assert transport[1] == []  # Saving/reading settings never probes the server.
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST)
    assert result.status_code == 200 and result.json()["status"] == "ok"
    assert len(transport[1]) == 1
    assert str(transport[1][0].url) == endpoint + "/v1/systemone"
    assert "Authorization" not in transport[1][0].headers


@pytest.mark.parametrize(
    "location,endpoint,model",
    [("cloud_direct", "https://ollama.com", "clef"),
     ("cloud_via_local", "http://localhost:11434", "clef:cloud"),
     ("cloud_via_local", "http://192.168.1.50:11434", "clef:cloud")],
)
def test_cloud_consent_applies_to_explicit_test_even_when_disabled(client, admin, transport, location, endpoint, model):
    configure(
        client,
        admin,
        enabled=False,
        cloud_opt_in=False,
        execution_location=location,
        endpoint=endpoint,
        model=model,
        api_key=SECRET if location == "cloud_direct" else "",
    )
    result = client.post(BASE + "/test", headers=admin).json()
    assert result["reason_code"] == "cloud_consent_required" and transport[1] == []
    client.put(BASE + "/config", headers=admin, json={"cloud_opt_in": True})
    result = client.post(BASE + "/test", headers=admin).json()
    assert result["status"] == "ok" and len(transport[1]) == 1
    sent = transport[1][0]
    assert (
        "Authorization" not in sent.headers
        if location == "cloud_via_local"
        else sent.headers["Authorization"] == "Bearer " + SECRET
    )
    assert json.loads(sent.content)["state"] == "A synthetic test item is an apple."


def test_shadow_flow_result_read_and_no_truth_writes(client, admin, transport):
    configure(client, admin)
    with SessionLocal() as db:
        from app.models import Base

        before = {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
            if table.name != "classifier_evaluations"
        }
    response = client.post(BASE + "/evaluations", headers=admin, json=REQUEST)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok" and result["answers"]["q_1"]["choice"] == "c_1"
    assert client.get(BASE + "/evaluations/" + result["id"], headers=admin).json() == result
    sent = transport[1][0]
    assert str(sent.url) == "https://ollama.com/v1/systemone"
    assert json.loads(sent.content) == {"model": "clef", "state": REQUEST["state"], "questions": REQUEST["questions"]}
    with SessionLocal() as db:
        after = {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
            if table.name != "classifier_evaluations"
        }
        assert after == before
        stored = json.dumps(db.scalar(select(ClassifierEvaluation)).result)
        assert all(text not in stored for text in (SECRET, REQUEST["state"], "Food or other?", "Food", "Other"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x["answers"].clear(),
        lambda x: x["answers"].update(q_2=x["answers"]["q_1"]),
        lambda x: x["answers"]["q_1"].update(type="noul"),
        lambda x: x["answers"]["q_1"].update(choice="unknown"),
        lambda x: x["answers"]["q_1"].update(choice="c_2"),
        lambda x: x["answers"]["q_1"]["probabilities"].update(c_1=float("nan")),
        lambda x: x["answers"]["q_1"]["probabilities"].update(c_1=float("inf")),
        lambda x: x["answers"]["q_1"]["probabilities"].update(c_1=True),
        lambda x: x["answers"]["q_1"]["probabilities"].update(c_1=1.5),
        lambda x: x["answers"]["q_1"]["probabilities"].pop("c_2"),
        lambda x: x["answers"]["q_1"].update(confidence=float("nan")),
        lambda x: x["answers"]["q_1"].update(confidence=True),
        lambda x: x["answers"]["q_1"].update(confidence=None),
        lambda x: x.update(model="another-model"),
    ],
)
def test_invalid_provider_contract_abstains(client, admin, monkeypatch, mutate):
    configure(client, admin)
    payload = copy.deepcopy(REPLY)
    mutate(payload)
    # Raw bytes allow NaN/Infinity transport fixtures; standard httpx json rejects them.
    monkeypatch.setattr(
        clef,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(payload))), trust_env=False
        ),
    )
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()
    assert result["status"] == "invalid" and result["answers"] == {}


@pytest.mark.parametrize(
    "code,reason",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (402, "billing_required"),
        (429, "rate_limited"),
        (500, "provider_error"),
        (302, "redirect_refused"),
    ],
)
def test_provider_errors_are_safe_and_never_follow_redirects(client, admin, transport, code, reason):
    configure(client, admin)
    install, calls = transport
    install(payload={"error": SECRET}, code=code, headers={"Location": "https://evil.example"})
    response = client.post(BASE + "/evaluations", headers=admin, json=REQUEST)
    assert response.json()["reason_code"] == reason and SECRET not in response.text
    assert len(calls) == 1


def test_timeout_no_retry_and_oversized_response(client, admin, transport):
    configure(client, admin)
    install, calls = transport
    install(error=httpx.ReadTimeout(SECRET))
    response = client.post(BASE + "/evaluations", headers=admin, json=REQUEST)
    assert response.json()["reason_code"] == "timeout" and SECRET not in response.text and len(calls) == 1
    install(payload={"padding": "x" * (clef.MAX_RESPONSE_BYTES + 1)})
    assert client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()["reason_code"] == "response_too_large"


@pytest.mark.parametrize(
    "bad",
    [
        {"enabled": "garbage"},
        {"enabled": "true", "execution_location": "local", "endpoint": "http://localhost:11434", "model": "clef:cloud"},
        {"enabled": "true", "provider": "evil"},
    ],
)
def test_corrupt_stored_config_safe_view_and_zero_inference(client, admin, transport, bad):
    with SessionLocal() as db:
        db.add(ConnectorConfig(key="classifier", ciphertext=encrypt_json(bad)))
        db.commit()
    assert client.get(BASE + "/config", headers=admin).json()["valid"] is False
    result = client.post(BASE + "/test", headers=admin).json()
    assert result["reason_code"] == "invalid_configuration" and transport[1] == []


def test_typed_yes_no_and_score_result_never_stores_descriptions(client, admin, transport):
    configure(client, admin)
    request = {
        "state": "synthetic",
        "questions": {
            "q_1": {"type": "noul", "instructions": "Is food?"},
            "q_2": {"type": "score", "instructions": "Urgency?", "criteria": ["Low private", "High private"]},
        },
    }
    transport[0](
        payload={
            "model": "clef",
            "answers": {
                "q_1": {"type": "noul", "noul": 0.8},
                "q_2": {
                    "type": "score",
                    "score": 0.7,
                    "probabilities": {"0": 0.3, "1": 0.7},
                    "confidence": 0.12,
                    "legend": {"0": "Low private", "1": "High private"},
                },
            },
        }
    )
    response = client.post(BASE + "/evaluations", headers=admin, json=request)
    assert response.json()["status"] == "ok" and "private" not in response.text


def test_expiry_row_cap_and_disconnect_clears_results(client, admin, monkeypatch):
    monkeypatch.setattr(classifier_repository, "MAX_RESULTS", 2)
    ids = [client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()["id"] for _ in range(3)]
    assert client.get(BASE + "/evaluations/" + ids[0], headers=admin).status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ClassifierEvaluation)) == 2
        row = db.get(ClassifierEvaluation, __import__("uuid").UUID(ids[-1]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(BASE + "/evaluations/" + ids[-1], headers=admin).status_code == 404
    client.delete(BASE + "/config", headers=admin)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ClassifierEvaluation)) == 0


def test_trickling_response_is_cancelled_and_closed_at_total_deadline(client, admin, monkeypatch):
    configure(client, admin)
    closed = []

    class Trickling(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await anyio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(clef, "TIMEOUT_SECONDS", 0.06)
    monkeypatch.setattr(
        clef,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Trickling()))),
    )
    started = time.monotonic()
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()
    assert result["reason_code"] == "timeout" and closed == [True]
    assert time.monotonic() - started < 1


def test_chunked_input_rejects_before_reading_remaining_private_body():
    from fastapi import HTTPException
    from starlette.requests import Request

    from modules.automation.api.classifier import _payload

    reads = []

    async def receive():
        reads.append(True)
        return {"type": "http.request", "body": b"x" * 25000, "more_body": True}

    async def exercise():
        with pytest.raises(HTTPException) as error:
            await _payload(Request({"type": "http"}, receive))
        assert error.value.status_code == 400 and SECRET not in str(error.value)

    anyio.run(exercise)
    assert len(reads) == 2


@pytest.mark.parametrize("value", [None, True, "0.8", float("nan"), float("inf"), -1, 2])
def test_yes_no_probability_requires_non_null_finite_number(value):
    request = EvaluationRequest.model_validate(
        {"state": "synthetic", "questions": {"q_1": {"type": "noul", "instructions": "Food?"}}}
    )
    with pytest.raises(ValueError):
        clef.validate_response(
            {"model": "clef", "answers": {"q_1": {"type": "noul", "noul": value}}}, request, ClassifierConfig()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "x" * 8001),
        ("state", " "),
        ("questions", {"private_name": REQUEST["questions"]["q_1"]}),
        ("questions", {f"q_{i}": REQUEST["questions"]["q_1"] for i in range(9)}),
    ],
)
def test_request_limits_reject_before_inference(client, admin, transport, field, value):
    configure(client, admin)
    request = {**REQUEST, field: value}
    response = client.post(BASE + "/evaluations", headers=admin, json=request)
    assert response.status_code == 400 and transport[1] == []


@pytest.mark.parametrize("bad", [{"enabled": True}, {"enabled": None}, {"api_key": [SECRET]}])
def test_corrupt_storage_types_do_not_get_silently_coerced(client, admin, transport, bad):
    with SessionLocal() as db:
        db.add(ConnectorConfig(key="classifier", ciphertext=encrypt_json(bad)))
        db.commit()
    response = client.get(BASE + "/config", headers=admin)
    assert response.json()["valid"] is False and SECRET not in response.text
    assert client.post(BASE + "/test", headers=admin).json()["reason_code"] == "invalid_configuration"
    assert transport[1] == []


@pytest.mark.parametrize("role,provisioned", [("user", True), ("admin", False)])
def test_non_admin_and_kiosk_tokens_cannot_access_classifier(client, role, provisioned):
    from app.models import AuthToken, User
    from app.services.auth import _password_record

    with SessionLocal() as db:
        user = User(
            email="restricted@example.com",
            password_hash=_password_record("ClassifierPass987!"),
            role=role,
            is_active=True,
            is_provisioned=provisioned,
        )
        db.add(user)
        db.flush()
        now = datetime.now(UTC)
        db.add(
            AuthToken(
                user_id=user.id,
                token="restricted-classifier-token",
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()
    headers = {"Authorization": "Bearer restricted-classifier-token"}
    assert client.get(BASE + "/config", headers=headers).status_code == 403
    assert client.post(BASE + "/test", headers=headers).status_code == 403


def test_storage_unavailable_fails_safely_without_mutation(client, admin, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "config_secret_key", "")
    assert client.get(BASE + "/config", headers=admin).json()["secrets_available"] is False
    response = client.put(BASE + "/config", headers=admin, json={"enabled": True, "api_key": SECRET})
    assert response.status_code == 409 and SECRET not in response.text
    with SessionLocal() as db:
        assert db.get(ConnectorConfig, "classifier") is None


def test_invalid_identity_update_preserves_original_secret_atomically(client, admin):
    configure(client, admin)
    response = client.put(
        BASE + "/config", headers=admin, json={"execution_location": "local", "endpoint": "http://169.254.169.254"}
    )
    assert response.status_code == 400
    with SessionLocal() as db:
        saved = connector_config.get_classifier_config(db)
        assert saved["api_key"] == SECRET and saved["endpoint"] == "https://ollama.com"


def test_new_migration_upgrade_and_downgrade_preserve_existing_data():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = importlib.import_module("migrations.versions.0059_classifier_evaluations")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_food (name TEXT)"))
        connection.execute(text("INSERT INTO existing_food VALUES ('Apple')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "classifier_evaluations" in inspect(connection).get_table_names()
            migration.downgrade()
        assert "classifier_evaluations" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT name FROM existing_food")) == "Apple"


def test_production_client_disables_environment_proxies_and_redirects(monkeypatch):
    real_client = httpx.AsyncClient
    recorded = []

    def factory(**kwargs):
        recorded.append(kwargs)
        return real_client(**kwargs, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=REPLY)))

    monkeypatch.setattr(clef.httpx, "AsyncClient", factory)
    result = clef.evaluate(ClassifierConfig(api_key=SECRET), EvaluationRequest.model_validate(REQUEST))
    assert result["q_1"]["choice"] == "c_1"
    assert recorded[0]["trust_env"] is False and recorded[0]["follow_redirects"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_classifier_is_not_a_legacy_receipt_or_nutrition_fallback(client, admin, transport, monkeypatch, enabled):
    import modules.automation
    from modules.catalog.services import barcode_providers, barcodes

    configure(client, admin, enabled=enabled)

    def missing_ocr(_image):
        raise RuntimeError("OCR is unavailable in this fixture")

    monkeypatch.setattr(modules.automation, "image_bytes_to_text", missing_ocr)
    location = client.post("/api/locations", json={"name": "Shadow Test Pantry"}, headers=admin).json()
    response = client.post(
        f"/api/receipts/preview-image?location_id={location['id']}",
        headers=admin,
        files={"file": ("synthetic.jpg", b"image", "image/jpeg")},
    )
    assert response.status_code == 503
    monkeypatch.setattr(
        barcodes,
        "lookup_external_merged",
        lambda barcode, **kw: barcode_providers.ProductResult(
            barcode=barcode, name="Synthetic Beans", source="fixture"
        ),
    )
    monkeypatch.setattr(barcodes, "estimate_nutrition_by_name", lambda *a, **k: None)
    item = client.post("/api/items", headers=admin, json={"name": "Synthetic Beans", "barcode": "9990001112223"}).json()
    assert client.post(f"/api/items/{item['id']}/rescan", headers=admin).status_code == 200
    assert transport[1] == []


def test_deeply_nested_input_and_provider_json_fail_safely(client, admin, monkeypatch):
    deep = "[" * 1500 + "0" + "]" * 1500
    response = client.put(BASE + "/config", headers=admin, content=deep)
    assert response.status_code == 400
    configure(client, admin)
    monkeypatch.setattr(
        clef,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=deep))),
    )
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()
    assert result["status"] == "invalid" and result["answers"] == {}


def test_huge_integer_probability_is_invalid_instead_of_server_error(client, admin, monkeypatch):
    configure(client, admin)
    payload = copy.deepcopy(REPLY)
    payload["answers"]["q_1"]["probabilities"]["c_1"] = 10**1000
    monkeypatch.setattr(
        clef,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
    )
    result = client.post(BASE + "/evaluations", headers=admin, json=REQUEST).json()
    assert result["status"] == "invalid" and result["answers"] == {}


def test_openapi_describes_typed_requests_without_dangling_refs_or_secret_output(client):
    document = client.get("/api/openapi.json").json()
    update = document["paths"][BASE + "/config"]["put"]["requestBody"]
    assert update["required"] is True
    config = update["content"]["application/json"]["schema"]
    assert not config.get("required") and config["additionalProperties"] is False
    assert config["properties"]["enabled"]["type"] == "boolean"
    assert config["properties"]["api_key"]["writeOnly"] is True
    assert config["properties"]["api_key"]["format"] == "password"
    assert all("default" not in field and '"null"' not in json.dumps(field) for field in config["properties"].values())
    evaluation = document["paths"][BASE + "/evaluations"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert set(evaluation["required"]) == {"state", "questions"}
    question = evaluation["properties"]["questions"]["additionalProperties"]
    assert question["properties"]["type"]["enum"] == ["choice", "noul", "score"]
    assert set(question["required"]) == {"type", "instructions"}
    assert "$ref" not in json.dumps(evaluation) and "$defs" not in json.dumps(evaluation)
    output_ref = document["components"]["schemas"]["ConfigView"]["properties"]["config"]["$ref"]
    output = document["components"]["schemas"][output_ref.rsplit("/", 1)[-1]]
    assert "api_key" not in output["properties"]
