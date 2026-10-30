"""Instacart shoppable-list scaffold (ADR 0013 Phase 4b).

Dormant until a Developer Platform key is configured. Tests cover the line-item
construction, the request built against the documented IDP contract (mocked HTTP,
since there's no live key), and the dormant 409.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from modules.shopping.connectors import instacart as ic
from modules.shopping.services import connector_config as cfg


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_line_items_include_all_pending_with_upc_when_present(client: httpx.Client) -> None:
    from app.db.session import engine
    from sqlalchemy.orm import Session

    from modules.shopping.services.shopping_list import instacart_line_items

    client.post("/api/shopping-list", json={"name": "Broccoli", "quantity": 1})  # no UPC → name only
    client.post(
        "/api/shopping-list/from-offer",
        json={"provider": "kroger", "name": "Kroger Beans", "upc": "0001111060903", "quantity": 2, "unit": "each"},
    )
    db = Session(bind=engine)
    items = instacart_line_items(db)
    db.close()

    names = {li["name"]: li for li in items}
    assert "Broccoli" in names and "upcs" not in names["Broccoli"]  # included by name, no UPC
    assert names["Kroger Beans"]["upcs"] == ["0001111060903"]  # UPC attached when present


def test_create_products_link_builds_documented_request(monkeypatch) -> None:
    seen: dict = {}

    def _post(url, json=None, headers=None, **k):
        seen["url"] = url
        seen["body"] = json
        seen["auth"] = headers["Authorization"]
        return _Resp(200, {"products_link_url": "https://instacart.com/store/list/abc"})

    monkeypatch.setattr(ic.httpx, "post", _post)
    link = ic.create_products_link(
        title="PantryOS shopping list",
        line_items=[{"name": "Milk", "quantity": 1, "unit": "each"}],
        api_key="KEY",
    )
    assert link == "https://instacart.com/store/list/abc"
    assert seen["url"].endswith("/idp/v1/products/products_link")
    assert seen["auth"] == "Bearer KEY"
    assert seen["body"]["link_type"] == "shopping_list"
    assert seen["body"]["line_items"][0]["name"] == "Milk"


def test_create_products_link_dormant_without_key() -> None:
    from modules.shopping.connectors import NotConfiguredError

    with pytest.raises(NotConfiguredError):
        ic.create_products_link(title="x", line_items=[{"name": "Milk"}], api_key="")


def test_instacart_route_is_409_until_configured(client: httpx.Client) -> None:
    client.post("/api/shopping-list", json={"name": "Broccoli", "quantity": 1})
    r = client.post("/api/shopping-list/instacart")
    assert r.status_code == 409  # dormant — no key configured


def test_instacart_route_returns_link_when_configured(client: httpx.Client, monkeypatch) -> None:
    from app.db.session import engine
    from sqlalchemy.orm import Session

    db = Session(bind=engine)
    cfg.set_config(db, "instacart", {"api_key": "KEY"})
    db.close()

    monkeypatch.setattr(
        ic.httpx, "post", lambda *a, **k: _Resp(200, {"products_link_url": "https://instacart.com/store/list/xyz"})
    )
    client.post("/api/shopping-list", json={"name": "Broccoli", "quantity": 1})

    r = client.post("/api/shopping-list/instacart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "https://instacart.com/store/list/xyz" and body["count"] >= 1
