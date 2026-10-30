"""Provider keys resolve DB-override-over-env, and are admin-configurable (ADR 0013)."""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.db import SessionLocal
from app.services import provider_keys
from modules.shopping.services import connector_config as cfg


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "KitchenBoss1"})
    tok = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "KitchenBoss1"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_usda_key_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "usda_fdc_api_key", "env-key")
    with SessionLocal() as db:
        assert provider_keys.usda_api_key(db) == "env-key"


def test_db_override_wins_over_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "usda_fdc_api_key", "env-key")
    with SessionLocal() as db:
        cfg.set_config(db, "usda", {"api_key": "admin-key"})
        assert provider_keys.usda_api_key(db) == "admin-key"


def test_kroger_credentials_resolve(monkeypatch) -> None:
    monkeypatch.setattr(settings, "kroger_client_id", "env-id")
    monkeypatch.setattr(settings, "kroger_client_secret", "env-secret")
    with SessionLocal() as db:
        assert provider_keys.kroger_credentials(db) == ("env-id", "env-secret")
        cfg.set_config(db, "kroger", {"client_id": "db-id", "client_secret": "db-secret"})
        assert provider_keys.kroger_credentials(db) == ("db-id", "db-secret")


def test_usda_is_admin_configurable_and_redacts_the_secret(client: httpx.Client) -> None:
    headers = _admin(client)
    put = client.put("/api/admin/integrations/usda", json={"updates": {"api_key": "s3cret"}}, headers=headers)
    assert put.status_code == 200, put.text

    view = client.get("/api/admin/integrations/usda", headers=headers).json()
    assert view["secret_set"]["api_key"] is True
    assert "s3cret" not in str(view)  # never leaks the value


def test_kroger_is_admin_configurable(client: httpx.Client) -> None:
    headers = _admin(client)
    put = client.put(
        "/api/admin/integrations/kroger",
        json={"updates": {"client_id": "cid", "client_secret": "csecret"}},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    view = client.get("/api/admin/integrations/kroger", headers=headers).json()
    assert view["values"]["client_id"] == "cid"
    assert view["secret_set"]["client_secret"] is True
    assert "csecret" not in str(view)
