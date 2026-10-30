"""Observable classifier contracts with isolated SQLite and transport fixtures.

These tests do not measure a live Clef model's accuracy or availability.
"""

from __future__ import annotations

import copy
import json

import anyio
import httpx
import pytest
from sqlalchemy import select

from app.core.crypto import decrypt_json
from app.db.session import SessionLocal
from app.models import ConnectorConfig
from modules.automation.providers import clef, jev

BASE = "/api/ai/classifier"
SECRET = "synthetic-openrouter-key-never-return"
REQUEST = {
    "state": "Private synthetic apple",
    "questions": {
        "q_1": {"type": "choice", "instructions": "Food or other?", "criteria": {"c_1": "Food", "c_2": "Other"}}
    },
}
REPLY = {
    "model": "typesafe/jev-1.14-20261014",
    "answers": {
        "q_1": {"type": "choice", "choice": "c_1", "probabilities": {"c_1": 0.9, "c_2": 0.1}, "confidence": 0.53}
    },
    "usage": {"input_tokens": 10, "output_tokens": 0, "cost": 0.000001},
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
            jev,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False),
        )
        return calls

    install()
    monkeypatch.setattr(clef, "_client", lambda: pytest.fail("Jev must not call Ollama"))
    return install, calls


def configure(client, admin):
    response = client.put(
        BASE + "/config",
        headers=admin,
        json={
            "enabled": True,
            "provider": "openrouter",
            "endpoint": "https://openrouter.ai",
            "model": "typesafe/jev-1.14",
            "execution_location": "cloud_direct",
            "api_key": SECRET,
            "cloud_opt_in": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"]["cloud_opt_in"] is False
    client.put(BASE + "/config", headers=admin, json={"cloud_opt_in": True})


def evaluate(client, admin, request=None):
    response = client.post(BASE + "/evaluations", headers=admin, json=REQUEST if request is None else request)
    assert response.status_code == 200, response.text
    return response.json()


def test_native_endpoint_key_usage_no_truth_writes(client, admin, transport):
    configure(client, admin)
    from app.models import Base

    with SessionLocal() as db:
        before = {
            t.name: [tuple(r) for r in db.execute(select(t))]
            for t in Base.metadata.sorted_tables
            if t.name != "classifier_evaluations"
        }
    result = evaluate(client, admin)
    assert result["status"] == "ok"
    assert result["model"] == "typesafe/jev-1.14" and result["resolved_model"] == REPLY["model"]
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 0, "cost_usd": 0.000001}
    assert result["provider_contract_version"] == "openrouter.decisions.2026-10-25"
    call = transport[1][0]
    assert str(call.url) == "https://openrouter.ai/api/alpha/decisions"
    assert call.headers["authorization"] == "Bearer " + SECRET
    assert json.loads(call.content) == {"model": "typesafe/jev-1.14", **REQUEST, "provider": {"allow_fallbacks": False}}
    assert "http-referer" not in call.headers
    assert client.get(BASE + "/evaluations/" + result["id"], headers=admin).json() == result
    assert SECRET not in json.dumps(result) and REQUEST["state"] not in json.dumps(result)
    with SessionLocal() as db:
        after = {
            t.name: [tuple(r) for r in db.execute(select(t))]
            for t in Base.metadata.sorted_tables
            if t.name != "classifier_evaluations"
        }
    assert before == after
    assert client.get("/api/ai/status", headers=admin).json()["active_vision_provider"] is None


def test_switch_isolates_keys_and_consent_both_directions(client, admin, transport):
    client.put(BASE + "/config", headers=admin, json={"api_key": "ollama-only", "cloud_opt_in": True})
    configure(client, admin)
    assert evaluate(client, admin)["status"] == "ok"
    response = client.put(
        BASE + "/config",
        headers=admin,
        json={"provider": "ollama", "endpoint": "https://ollama.com", "model": "clef", "cloud_opt_in": True},
    )
    assert response.json()["api_key_set"] is False and response.json()["config"]["cloud_opt_in"] is False
    client.put(BASE + "/config", headers=admin, json={"cloud_opt_in": True})
    assert evaluate(client, admin)["reason_code"] == "not_configured"
    assert len(transport[1]) == 1
    with SessionLocal() as db:
        assert decrypt_json(db.get(ConnectorConfig, "classifier").ciphertext)["api_key"] == ""


@pytest.mark.parametrize(
    "change",
    [
        {"model": "typesafe/jev-router"},
        {"model": "~typesafe/jev-latest"},
        {"model": "typesafe/jev-1.13"},
        {"execution_location": "local"},
        {"endpoint": "https://openrouter.ai.evil.invalid"},
        {"endpoint": "https://openrouter.ai/api"},
        {"endpoint": "http://openrouter.ai"},
        {"endpoint": "https://openrouter.ai?x=1"},
        {"endpoint": "https://user:secret@example.com"},
        {"endpoint": "https://openrouter.ai:444"},
    ],
)
def test_invalid_config_atomic_redacted(client, admin, transport, change):
    configure(client, admin)
    before = client.get(BASE + "/config", headers=admin).json()
    response = client.put(BASE + "/config", headers=admin, json={**change, "api_key": "replacement-private"})
    assert response.status_code == 400 and "replacement-private" not in response.text
    assert client.get(BASE + "/config", headers=admin).json() == before
    assert evaluate(client, admin)["status"] == "ok"
    assert transport[1][-1].headers["authorization"] == "Bearer " + SECRET


@pytest.mark.parametrize("model", ["typesafe/jev-1.14", "typesafe/jev-1.14-20261014"])
def test_same_version_snapshot(client, admin, transport, model):
    configure(client, admin)
    transport[0]({**REPLY, "model": model})
    assert evaluate(client, admin)["resolved_model"] == model


@pytest.mark.parametrize(
    "model",
    [
        "typesafe/jev-1.140",
        "typesafe/jev-1.14-evil",
        "typesafe/jev-1.14-20261301",
        "typesafe/jev-1.14-202610140",
        "typesafe/jev-1.13-20260917",
        "clef",
        None,
        14,
    ],
)
def test_wrong_model_rejected_no_fallback(client, admin, transport, model):
    configure(client, admin)
    transport[0]({**REPLY, "model": model})
    result = evaluate(client, admin)
    assert result["status"] == "invalid" and result["answers"] == {}
    assert "resolved_model" not in result and len(transport[1]) == 1


@pytest.mark.parametrize(
    "usage,expected",
    [(None, None), ({}, None), ({"input_tokens": 9}, {"input_tokens": 9}), ({"cost": 0}, {"cost_usd": 0})],
)
def test_missing_usage_unknown_not_zero(client, admin, transport, usage, expected):
    configure(client, admin)
    transport[0]({**REPLY, "usage": usage})
    result = evaluate(client, admin)
    assert result["status"] == "ok" and result.get("usage") == expected


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": True},
        {"output_tokens": -1},
        {"input_tokens": 1.5},
        {"cost": "0.01"},
        {"cost": -1},
        {"cost": True},
        {"cost": None},
        {"input_tokens": 10**1000},
    ],
)
def test_invalid_usage_rejected(client, admin, transport, usage):
    configure(client, admin)
    transport[0]({**REPLY, "usage": usage})
    assert evaluate(client, admin)["status"] == "invalid"


@pytest.mark.parametrize(
    "code,reason",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (402, "billing_required"),
        (429, "rate_limited"),
        (413, "provider_input_limit"),
        (524, "timeout"),
        (503, "provider_error"),
        (302, "redirect_refused"),
    ],
)
def test_failure_normalized_no_reroute(client, admin, transport, code, reason):
    configure(client, admin)
    transport[0]({"error": {"message": SECRET + REQUEST["state"]}}, code)
    result = evaluate(client, admin)
    assert result["status"] == "unavailable" and result["reason_code"] == reason
    assert result["answers"] == {} and len(transport[1]) == 1
    assert SECRET not in json.dumps(result)


def test_all_primitives_safe_legend_discard(client, admin, transport):
    configure(client, admin)
    request = copy.deepcopy(REQUEST)
    request["questions"].update(
        {
            "q_2": {"type": "noul", "instructions": "Edible?"},
            "q_3": {"type": "score", "instructions": "Ripe?", "criteria": ["Unripe private", "Ripe private"]},
        }
    )
    reply = copy.deepcopy(REPLY)
    reply["answers"].update(
        {
            "q_2": {"type": "noul", "noul": 0.7},
            "q_3": {
                "type": "score",
                "score": 0.8,
                "confidence": 0.4,
                "probabilities": {"0": 0.2, "1": 0.8},
                "legend": {"0": "Unripe private", "1": "Ripe private"},
            },
        }
    )
    transport[0](reply)
    result = evaluate(client, admin, request)
    assert result["status"] == "ok" and result["answers"]["q_2"]["noul"] == 0.7
    assert result["answers"]["q_3"]["score"] == 0.8 and "Ripe private" not in json.dumps(result)


def test_disabled_test_disconnect(client, admin, transport):
    configure(client, admin)
    client.put(BASE + "/config", headers=admin, json={"enabled": False, "api_key": ""})
    assert evaluate(client, admin)["reason_code"] == "disabled" and not transport[1]
    assert client.post(BASE + "/test", headers=admin).json()["status"] == "ok"
    assert json.loads(transport[1][0].content)["state"] != REQUEST["state"]
    view = client.delete(BASE + "/config", headers=admin).json()
    assert view["config"]["provider"] == "ollama" and view["api_key_set"] is False
    assert evaluate(client, admin)["reason_code"] == "disabled" and len(transport[1]) == 1


def test_deadline_cancels_trickling_stream(client, admin, transport, monkeypatch):
    configure(client, admin)
    closed = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await anyio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(jev, "TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        jev,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=SlowStream())), trust_env=False
        ),
    )
    assert evaluate(client, admin)["reason_code"] == "timeout" and closed


def test_switch_without_new_key_never_reuses_ollama_secret(client, admin, transport):
    client.put(
        BASE + "/config", headers=admin, json={"enabled": True, "api_key": "ollama-private-key", "cloud_opt_in": True}
    )
    switched = client.put(
        BASE + "/config",
        headers=admin,
        json={
            "provider": "openrouter",
            "endpoint": "https://openrouter.ai",
            "model": "typesafe/jev-1.14",
            "api_key": "",
        },
    ).json()
    assert switched["api_key_set"] is False and switched["configured"] is False
    assert switched["config"]["cloud_opt_in"] is False
    client.put(BASE + "/config", headers=admin, json={"cloud_opt_in": True})
    assert evaluate(client, admin)["reason_code"] == "not_configured" and not transport[1]


@pytest.mark.parametrize(
    "mutation",
    [
        {"q_1": {"type": "noul", "noul": 0.9}},
        {},
        {"q_1": {"type": "choice", "choice": "c_3", "confidence": 0.5, "probabilities": {"c_1": 0.9, "c_2": 0.1}}},
        {"q_1": {"type": "choice", "choice": "c_1", "confidence": True, "probabilities": {"c_1": 0.9, "c_2": 0.1}}},
    ],
)
def test_jev_schema_mismatch_has_no_answers(client, admin, transport, mutation):
    configure(client, admin)
    transport[0]({**REPLY, "answers": mutation})
    result = evaluate(client, admin)
    assert result["status"] == "invalid" and result["answers"] == {} and "usage" not in result
