"""Productization seam: free mode fully works, connected mode is a safe stub (ADR 0012/0018)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.entitlement import (
    ALL_FEATURES,
    FEATURE_CLOUD_AI,
    FEATURE_CORE,
    FEATURE_STORE_INTEGRATIONS,
    MODE_CONNECTED,
    MODE_FREE,
    check_brokered_feature,
    deployment_mode,
    require_feature,
    resolve_entitlement,
)
from app.services.integration_broker import (
    BrokerUnavailable,
    HttpBroker,
    NullBroker,
    get_broker,
)


@pytest.fixture(autouse=True)
def _free_mode(monkeypatch):
    """Default every test to free mode; connected tests opt in explicitly."""
    monkeypatch.setattr(settings, "license_server_url", "")
    monkeypatch.setattr(settings, "integration_broker_url", "")


def test_free_mode_is_the_default_and_grants_core() -> None:
    assert deployment_mode() == MODE_FREE
    ent = resolve_entitlement()
    assert ent.mode == MODE_FREE
    assert ent.tier == "free"
    assert ent.valid is True
    assert ent.has(FEATURE_CORE) is True
    assert ent.has(FEATURE_STORE_INTEGRATIONS) is False


def test_connected_mode_fails_safe_to_free_until_backend_exists(monkeypatch) -> None:
    monkeypatch.setattr(settings, "license_server_url", "https://api.pantryos.example")
    ent = resolve_entitlement()
    # Mode reflects the config, but no premium is unlocked without real validation.
    assert deployment_mode() == MODE_CONNECTED
    assert ent.mode == MODE_CONNECTED
    assert ent.valid is True
    assert ent.has(FEATURE_CORE) is True
    assert ent.has(FEATURE_STORE_INTEGRATIONS) is False


def test_require_feature_allows_core_and_blocks_premium_in_free_mode() -> None:
    # Core: no exception.
    require_feature(FEATURE_CORE)()

    with pytest.raises(HTTPException) as exc_info:
        require_feature(FEATURE_STORE_INTEGRATIONS)()
    err = exc_info.value
    assert err.status_code == 402
    assert err.detail["error"] == "feature_not_available"
    assert err.detail["feature"] == FEATURE_STORE_INTEGRATIONS


def test_broker_is_null_in_free_mode() -> None:
    broker = get_broker()
    assert isinstance(broker, NullBroker)
    assert broker.is_available() is False
    with pytest.raises(BrokerUnavailable):
        broker.authorize_url(None, "kroger", redirect_back="y")


def test_broker_is_http_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integration_broker_url", "https://broker.pantryos.example/")
    broker = get_broker()
    assert isinstance(broker, HttpBroker)
    assert broker.is_available() is True
    assert broker.base_url == "https://broker.pantryos.example"  # trailing slash trimmed


def test_entitlement_endpoint_reports_free_mode(client: httpx.Client) -> None:
    resp = client.get("/api/entitlement")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == MODE_FREE
    assert body["tier"] == "free"
    assert body["valid"] is True
    assert FEATURE_CORE in body["features"]
    assert FEATURE_STORE_INTEGRATIONS not in body["features"]
    assert FEATURE_STORE_INTEGRATIONS in body["all_features"]
    assert body["integrations_brokered"] is False


def test_entitlement_endpoint_needs_no_auth(client: httpx.Client, monkeypatch) -> None:
    # Ungated: readable even with auth on, so the frontend can decide which
    # premium surfaces to show before any login.
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/entitlement").status_code == 200


def test_cloud_ai_is_a_known_premium_feature() -> None:
    assert FEATURE_CLOUD_AI in ALL_FEATURES
    ent = resolve_entitlement()
    assert ent.has(FEATURE_CLOUD_AI) is False  # free mode


def test_check_brokered_feature_is_noop_without_a_broker() -> None:
    # Pure self-host: no broker configured → the caller falls through to its
    # ordinary "not configured" error, never a 402 (ADR 0018 §3).
    check_brokered_feature(FEATURE_STORE_INTEGRATIONS)


def test_check_brokered_feature_402s_when_brokered_but_unentitled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "integration_broker_url", "https://broker.pantryos.example")
    with pytest.raises(HTTPException) as exc_info:
        check_brokered_feature(FEATURE_STORE_INTEGRATIONS)
    assert exc_info.value.status_code == 402


def test_legacy_license_settings_never_gate_routes(client: httpx.Client, monkeypatch) -> None:
    # ADR 0018: the shared-password gate is gone. Even with the deprecated env
    # knobs set, feature modules answer without any X-License-Key header, and
    # the removed endpoints stay removed.
    monkeypatch.setenv("ENFORCE_LICENSE", "true")
    monkeypatch.setenv("LICENSE_KEY", "some-key")
    assert client.get("/api/items").status_code == 200
    assert client.get("/api/license/status").status_code == 404
    assert client.post("/api/license/validate", json={"license_key": "x"}).status_code == 404
