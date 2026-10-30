"""Kroger Cart fulfillment — Phase 4 of ADR 0013.

Covers the encrypted token store, the authorization_code/refresh flow, the
one-shot OAuth state, the cart-push body, and the buy-list → cart split. HTTP
calls to Kroger are stubbed; the public OAuth callback and the kiosk cart route
are exercised through the app.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine
from modules.shopping.services import kroger_cart as kc


@pytest.fixture(autouse=True)
def _personal(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)


def _session() -> Session:
    return Session(bind=engine)


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


# ---- OAuth state / token lifecycle -----------------------------------------


def test_oauth_state_is_one_shot() -> None:
    db = _session()
    kc.save_oauth_state(db, "abc")
    assert kc.verify_and_consume_state(db, "abc") is True
    assert kc.verify_and_consume_state(db, "abc") is False  # consumed
    assert kc.verify_and_consume_state(db, "") is False
    db.close()


def test_exchange_code_stores_and_reads_token(monkeypatch) -> None:
    db = _session()
    monkeypatch.setattr(
        kc.httpx,
        "post",
        lambda *a, **k: _Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 1800}),
    )
    kc.exchange_code(db, code="c", client_id="id", client_secret="sec", redirect_uri="https://h/cb")
    assert kc.is_connected(db) is True
    assert kc.get_access_token(db, client_id="id", client_secret="sec") == "AT"
    db.close()


def test_get_access_token_refreshes_when_expired(monkeypatch) -> None:
    db = _session()
    kc._write_row(
        db, kc._TOKEN_KEY, {"access_token": "OLD", "refresh_token": "RT", "expires_at": time.time() - 10}
    )
    seen: dict = {}

    def _post(url, data=None, **k):
        seen["grant"] = data["grant_type"]
        return _Resp(200, {"access_token": "NEW", "expires_in": 1800})  # note: no refresh_token echoed

    monkeypatch.setattr(kc.httpx, "post", _post)
    assert kc.get_access_token(db, client_id="id", client_secret="sec") == "NEW"
    assert seen["grant"] == "refresh_token"
    assert kc._read_row(db, kc._TOKEN_KEY)["refresh_token"] == "RT"  # prior refresh token preserved
    db.close()


def test_get_access_token_raises_when_not_connected() -> None:
    db = _session()
    with pytest.raises(kc.KrogerNotConnected):
        kc.get_access_token(db, client_id="id", client_secret="sec")
    db.close()


def test_disconnect_clears_token(monkeypatch) -> None:
    db = _session()
    kc._write_row(db, kc._TOKEN_KEY, {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 9e9})
    assert kc.is_connected(db) is True
    kc.disconnect(db)
    assert kc.is_connected(db) is False
    db.close()


# ---- Cart push --------------------------------------------------------------


def test_add_to_cart_body_and_errors(monkeypatch) -> None:
    db = _session()
    kc._write_row(db, kc._TOKEN_KEY, {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 9e9})
    seen: dict = {}

    def _put(url, json=None, headers=None, **k):
        seen["json"] = json
        seen["auth"] = headers["Authorization"]
        return _Resp(204)

    monkeypatch.setattr(kc.httpx, "put", _put)
    kc.add_to_cart(db, [kc.CartItem("0001", 2), kc.CartItem("0002", 1)], client_id="id", client_secret="sec")
    assert seen["json"] == {"items": [{"upc": "0001", "quantity": 2}, {"upc": "0002", "quantity": 1}]}
    assert seen["auth"] == "Bearer AT"

    seen.clear()
    kc.add_to_cart(db, [], client_id="id", client_secret="sec")  # empty → no HTTP call
    assert seen == {}

    monkeypatch.setattr(kc.httpx, "put", lambda *a, **k: _Resp(500, text="boom"))
    with pytest.raises(kc.KrogerCartError):
        kc.add_to_cart(db, [kc.CartItem("0001", 1)], client_id="id", client_secret="sec")
    db.close()


def test_kroger_upc_normalisation() -> None:
    from modules.shopping.services.shopping_list import _kroger_upc

    assert _kroger_upc("011110819222") == "0011110819222"  # 12 → 13 (the CART-2012 fix)
    assert _kroger_upc("0001111060903") == "0001111060903"  # already 13
    assert _kroger_upc("00011110819222") == "0011110819222"  # GTIN-14 leading zero → 13
    assert _kroger_upc("4688") == "0000000004688"  # PLU → padded
    assert _kroger_upc("abc") is None
    assert _kroger_upc("") is None


def test_cart_quantity_measured_amounts_become_package_counts() -> None:
    from types import SimpleNamespace

    from modules.shopping.services.shopping_list import _cart_quantity

    def item(size_qty=None, size_unit=None, density=None, gpe=None):
        return SimpleNamespace(
            size_quantity=size_qty, size_unit=size_unit, density_g_per_ml=density, grams_per_each=gpe
        )

    # The reported bug: 28 oz needed of a 28 oz can → 1 can, not 28.
    assert _cart_quantity({"quantity": "28", "unit": "oz"}, item(28, "oz")) == 1
    assert _cart_quantity({"quantity": "15", "unit": "oz"}, item("15.5", "oz")) == 1  # kidney beans
    assert _cart_quantity({"quantity": "56", "unit": "oz"}, item(28, "oz")) == 2  # genuinely two cans

    # Count units are already package counts.
    assert _cart_quantity({"quantity": "1", "unit": "each"}, item()) == 1
    assert _cart_quantity({"quantity": "3", "unit": "each"}, item()) == 3

    # Un-convertible measured amounts fall back to one package (never the measure).
    assert _cart_quantity({"quantity": "2", "unit": "tbsp"}, item(12, "oz")) == 1  # no density bridge
    assert _cart_quantity({"quantity": "3", "unit": "clove"}, item(1, "ct")) == 1  # unknown unit
    assert _cart_quantity({"quantity": "28", "unit": "oz"}, item()) == 1  # no pack size


def test_add_to_cart_isolates_a_bad_item(monkeypatch) -> None:
    db = _session()
    kc._write_row(db, kc._TOKEN_KEY, {"access_token": "AT", "refresh_token": "RT", "expires_at": time.time() + 9e9})

    def _put(url, json=None, headers=None, **k):
        upcs = [i["upc"] for i in json["items"]]
        if len(upcs) > 1:
            return _Resp(400, {"errors": {"reason": "bad batch"}})  # all-or-nothing failure
        if upcs[0] == "bad":
            return _Resp(400, {"errors": {"reason": "UPC not found"}})
        return _Resp(204)

    monkeypatch.setattr(kc.httpx, "put", _put)
    failed = kc.add_to_cart(
        db, [kc.CartItem("0000000000good", 1), kc.CartItem("bad", 1)], client_id="id", client_secret="sec"
    )
    assert failed == [{"upc": "bad", "reason": "UPC not found"}]  # good one still carted, bad one reported
    db.close()


def test_app_origin() -> None:
    assert kc.app_origin("https://pantry.ts.net/api/integrations/kroger/oauth/callback") == "https://pantry.ts.net"
    assert kc.app_origin("") == ""


# ---- HTTP surface -----------------------------------------------------------


def test_cart_route_needs_a_connected_account(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kroger_client_id", "id")
    monkeypatch.setattr(settings, "kroger_client_secret", "sec")
    # A store-matched entry (chosen offer with a UPC) so the push reaches the token check.
    client.post(
        "/api/shopping-list/from-offer",
        json={"provider": "kroger", "name": "Kroger Beans", "upc": "0001111060903", "quantity": 1, "unit": "each"},
    )
    r = client.post("/api/shopping-list/kroger-cart")
    assert r.status_code == 409  # creds present, but no Kroger account linked


def test_cart_route_pushes_and_splits(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kroger_client_id", "id")
    monkeypatch.setattr(settings, "kroger_client_secret", "sec")
    from modules.shopping.services import kroger_cart as kcsvc

    pushed: dict = {}
    monkeypatch.setattr(kcsvc, "add_to_cart", lambda db, items, **k: (pushed.update(items=items), [])[1])

    # One entry that carries a UPC (via from-offer), one loose need without one.
    client.post(
        "/api/shopping-list/from-offer",
        json={"provider": "kroger", "name": "Kroger Beans", "upc": "0001111060903", "quantity": 2, "unit": "each"},
    )
    client.post("/api/shopping-list", json={"name": "Paper Towels", "quantity": 1})

    r = client.post("/api/shopping-list/kroger-cart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(a["upc"] == "0001111060903" and a["quantity"] == 2 for a in body["added"])
    assert any(s["name"] == "Paper Towels" for s in body["skipped"])
    assert len(pushed["items"]) == 1


def test_oauth_callback_success_redirects_and_stores(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kroger_client_id", "id")
    monkeypatch.setattr(settings, "kroger_client_secret", "sec")
    monkeypatch.setattr(
        settings, "kroger_redirect_uri", "https://pantry.ts.net/api/integrations/kroger/oauth/callback"
    )
    db = _session()
    kc.save_oauth_state(db, "xyz")
    db.close()
    monkeypatch.setattr(
        kc.httpx,
        "post",
        lambda *a, **k: _Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 1800}),
    )

    r = client.get(
        "/api/integrations/kroger/oauth/callback", params={"code": "c", "state": "xyz"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://pantry.ts.net/admin/integrations?kroger=connected"

    db = _session()
    assert kc.is_connected(db) is True
    db.close()


def test_oauth_callback_bad_state_redirects_to_error(client, monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "kroger_redirect_uri", "https://pantry.ts.net/api/integrations/kroger/oauth/callback"
    )
    r = client.get(
        "/api/integrations/kroger/oauth/callback", params={"code": "c", "state": "nope"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert r.headers["location"].endswith("?kroger=error")
