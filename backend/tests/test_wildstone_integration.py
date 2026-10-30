"""Wildstone Home Lab integration — ADR 0026.

Three things pinned here:
  * contract ``pantry-1``: answers in Wildstone's result types, keeps the
    ``fake-1`` field names, never reports "empty" for a mistake, and is
    repeat-safe (milk goes on the list once);
  * the relay: connecting starts at "now", each fact becomes exactly one
    envelope, and re-reading queues nothing new;
  * delivery: delivered / rejected / retry-later, and a failure stops the batch.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.db.session import SessionLocal
from modules.wildstone.models import WildstoneOutbox
from modules.wildstone.services.delivery import deliver_pending
from modules.wildstone.services.relay import run_pass

BASE = "/api/integrations/wildstone"
V1 = f"{BASE}/v1"


def _admin_headers(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    login = client.post("/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _agent(client: httpx.Client, h: dict, *, scopes: str = "read,write") -> dict[str, str]:
    created = client.post("/api/agent-keys", json={"name": "Wildstone", "scopes": scopes}, headers=h)
    assert created.status_code == 200, created.text
    return {"X-Agent-Key": created.json()["secret"]}


def _connect(client: httpx.Client, h: dict) -> None:
    resp = client.put(
        f"{BASE}/connection",
        json={"keystone_url": "https://wildstone.test/", "publisher_token": "pub-secret"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text


def _pass(**kwargs) -> int:
    kwargs.setdefault("receipt_quiet", timedelta(0))
    with SessionLocal() as db:
        return run_pass(db, settle=timedelta(0), **kwargs)


def _outbox() -> list[dict]:
    with SessionLocal() as db:
        return [
            {"type": row.event_type, "status": row.status, "envelope": row.envelope, "attempts": row.attempts}
            for row in db.query(WildstoneOutbox).order_by(WildstoneOutbox.id).all()
        ]


# --- contract ------------------------------------------------------------------


def test_capabilities_and_read_only_key(client: httpx.Client) -> None:
    h = _admin_headers(client)
    reader = _agent(client, h, scopes="read")

    caps = client.get(f"{V1}/capabilities", headers=reader)
    assert caps.status_code == 200
    body = caps.json()
    assert body["contract"] == "pantry-1"
    names = {c["name"]: c for c in body["capabilities"]}
    # The fake-1 names survive, so Wildstone's contract tests carry over.
    assert {"pantry.list", "pantry.inventory", "pantry.add"} <= set(names)
    assert names["pantry.cook.finish"]["proposedRisk"] == "confirm"

    # A read-only key can ask, but not act.
    assert client.get(f"{V1}/query/pantry.list?list=groceries", headers=reader).status_code == 200
    denied = client.post(f"{V1}/act/pantry.add", json={"list": "groceries", "item": "milk"}, headers=reader)
    assert denied.status_code == 403


def test_grocery_list_is_repeat_safe_and_empty_is_not_an_error(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)

    empty = client.get(f"{V1}/query/pantry.list?list=groceries", headers=agent).json()
    assert empty["status"] == "empty"
    assert empty["data"] == {"list": "groceries", "items": [], "entries": []}
    assert empty["observedAt"].endswith("Z")

    first = client.post(f"{V1}/act/pantry.add", json={"list": "groceries", "item": "Milk"}, headers=agent).json()
    assert first["status"] == "ok" and first["data"]["added"] is True
    again = client.post(f"{V1}/act/pantry.add", json={"list": "groceries", "item": "milk"}, headers=agent).json()
    assert again["data"]["added"] is False and again["data"]["reason"] == "already on the list"

    listed = client.get(f"{V1}/query/pantry.list?list=shopping", headers=agent).json()
    assert listed["status"] == "ok" and listed["data"]["items"] == ["Milk"]

    checked = client.post(f"{V1}/act/pantry.check", json={"list": "groceries", "item": "milk"}, headers=agent).json()
    assert checked["data"]["checked"] is True
    assert client.get(f"{V1}/query/pantry.list?list=groceries", headers=agent).json()["status"] == "empty"


def test_mistakes_are_loud(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)
    # Another list is Wildstone's, not ours: a routing bug must not look empty.
    wrong = client.get(f"{V1}/query/pantry.list?list=hardware", headers=agent)
    assert wrong.status_code == 422
    assert client.get(f"{V1}/query/pantry.nope", headers=agent).status_code == 404
    assert client.post(f"{V1}/act/pantry.nope", json={}, headers=agent).status_code == 404
    assert client.post(f"{V1}/act/pantry.add", json={"list": "groceries"}, headers=agent).status_code == 422
    assert client.post(f"{V1}/act/pantry.cook.step", json={"action": "next"}, headers=agent).status_code == 422


def test_inventory_and_cook_along_through_the_contract(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = client.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc, "quantity": 100, "unit": "g"}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={
            "name": "Flatbread",
            "servings": 4,
            "instructions": "1. Mix the flour.\n2. Bake.",
            "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}],
        },
        headers=h,
    ).json()

    inv = client.get(f"{V1}/query/pantry.inventory?search=flo", headers=agent).json()
    assert inv["status"] == "ok"
    assert inv["data"]["inventory"][0]["item"] == "Flour"
    assert inv["data"]["inventory"][0]["count"] == 100
    assert inv["data"]["inventory"][0]["locations"] == ["Pantry"]

    steps = client.get(f"{V1}/query/pantry.recipe?recipe_id={recipe['id']}", headers=agent).json()
    assert steps["data"]["steps"] == ["Mix the flour.", "Bake."]

    assert client.get(f"{V1}/query/pantry.cook.current", headers=agent).json()["status"] == "empty"
    started = client.post(f"{V1}/act/pantry.cook.start", json={"recipe_id": recipe["id"], "servings": 2}, headers=agent)
    assert started.json()["data"]["session"]["step_text"] == "Mix the flour."
    nxt = client.post(f"{V1}/act/pantry.cook.step", json={"action": "next"}, headers=agent).json()
    assert nxt["data"]["session"]["step_text"] == "Bake."
    current = client.get(f"{V1}/query/pantry.cook.current", headers=agent).json()
    assert current["data"]["session"]["step_number"] == 2

    done = client.post(f"{V1}/act/pantry.cook.finish", json={"location_id": loc}, headers=agent).json()
    assert done["data"]["session"]["status"] == "finished"
    after = client.get(f"{V1}/query/pantry.inventory", headers=agent).json()
    assert after["data"]["inventory"][0]["count"] == 96

    # The response was lost and the agent retries: it gets the cook already
    # recorded — same result, nothing taken twice — not "nothing is cooking".
    again = client.post(f"{V1}/act/pantry.cook.finish", json={"location_id": loc}, headers=agent).json()
    assert again["status"] == "ok", again
    assert again["data"]["cook"]["already_finished"] is True
    assert again["data"]["cook"]["cook_event_id"] == done["data"]["cook"]["cook_event_id"]
    assert client.get(f"{V1}/query/pantry.inventory", headers=agent).json()["data"]["inventory"][0]["count"] == 96


# --- connection ----------------------------------------------------------------


def test_connection_needs_a_token_and_never_returns_it(client: httpx.Client) -> None:
    h = _admin_headers(client)
    missing = client.put(f"{BASE}/connection", json={"keystone_url": "https://wildstone.test"}, headers=h)
    assert missing.status_code == 400
    bad_url = client.put(f"{BASE}/connection", json={"keystone_url": "wildstone", "publisher_token": "x"}, headers=h)
    assert bad_url.status_code == 400

    _connect(client, h)
    status = client.get(f"{BASE}/connection", headers=h).json()
    assert status["connected"] is True
    assert status["keystone_url"] == "https://wildstone.test"
    assert status["publisher_token_set"] is True
    assert "pub-secret" not in str(status)

    assert client.delete(f"{BASE}/connection", headers=h).json()["connected"] is False
    # An agent key is not an admin login.
    agent = _agent(client, h)
    assert client.get(f"{BASE}/connection", headers=agent).status_code == 401


# --- relay ---------------------------------------------------------------------


def test_relay_turns_facts_into_one_envelope_each(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    eggs = client.post("/api/items", json={"name": "Eggs", "restock_threshold": 3}, headers=h).json()["id"]
    # History from before connecting is not news.
    client.post("/api/inventory/add", json={"item_id": eggs, "location_id": loc, "quantity": 4, "unit": "each"}, headers=h)

    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    _connect(client, h)
    linked = client.put(f"{BASE}/members/{profile}", json={"member": "sam"}, headers=h)
    assert linked.status_code == 200 and linked.json()["member"] == "sam"

    assert _pass() == 0

    # Groceries arrive (one event for the batch), someone eats two eggs (a meal,
    # and eggs drop below 3), a lot is about to expire.
    milk = client.post("/api/items", json={"name": "Milk"}, headers=h).json()["id"]
    soon = (date.today() + timedelta(days=2)).isoformat()
    client.post(
        "/api/inventory/add",
        json={"item_id": milk, "location_id": loc, "quantity": 1, "unit": "each", "expiration_date": soon},
        headers=h,
    )
    plate = client.post(
        "/api/consumption/plate",
        json={"item_id": eggs, "location_id": loc, "quantity": 2, "unit": "each", "meal_type": "breakfast"},
        headers={**h, "X-Household-Profile": profile},
    )
    assert plate.status_code == 200, plate.text

    assert _pass() == 4
    events = {e["type"]: e["envelope"] for e in _outbox()}
    assert set(events) == {
        "pantry.purchase_recorded",
        "pantry.meal_logged",
        "pantry.stock_low",
        "pantry.item_expiring",
    }

    meal = events["pantry.meal_logged"]
    assert meal["subject"] == "sam"
    assert meal["payload"]["food"] == "Eggs" and meal["payload"]["meal_type"] == "breakfast"
    assert "calories" not in meal["payload"]
    assert meal["schemaVersion"] == 1 and meal["occurredAt"].endswith("Z")

    assert events["pantry.purchase_recorded"]["payload"]["items"][0]["item"] == "Milk"
    assert events["pantry.stock_low"]["payload"]["on_hand"] == 2
    assert events["pantry.item_expiring"]["payload"]["days_left"] == 2

    # Reading again queues nothing: same facts, same ids — and still-low stock
    # and the already-scanned day do not repeat.
    assert _pass() == 0
    assert len({e["envelope"]["eventId"] for e in _outbox()}) == len(_outbox())


def test_unlinked_meal_has_no_subject_and_cook_is_reported(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = client.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()
    client.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc, "quantity": 100, "unit": "g"}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={"name": "Flatbread", "servings": 4, "instructions": "1. Mix.\n2. Bake.",
              "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}]},
        headers=h,
    ).json()
    profile = client.post("/api/household/profiles", json={"display_name": "Kid"}, headers=h).json()["id"]
    _connect(client, h)
    _pass()

    client.post(f"/api/recipes/{recipe['id']}/cook", json={"servings_made": 4, "location_id": loc}, headers=h)
    client.post(
        "/api/consumption/plate",
        json={"item_id": item["id"], "location_id": loc, "quantity": 5, "unit": "g"},
        headers={**h, "X-Household-Profile": profile},
    )
    _pass()
    events = {e["type"]: e["envelope"] for e in _outbox()}
    assert events["pantry.recipe_cooked"]["payload"]["recipe"] == "Flatbread"
    assert events["pantry.recipe_cooked"]["payload"]["servings_made"] == 4
    assert "subject" not in events["pantry.meal_logged"]
    # A recipe cook's ingredient removals are not meals.
    assert sum(1 for e in _outbox() if e["type"] == "pantry.meal_logged") == 1
    assert _pass() == 0


def test_stock_low_fires_again_only_after_recovering(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    rice = client.post("/api/items", json={"name": "Rice", "restock_threshold": 5}, headers=h).json()["id"]
    _connect(client, h)  # rice is already low: starting picture, not news
    assert _pass() == 0

    client.post("/api/inventory/add", json={"item_id": rice, "location_id": loc, "quantity": 10, "unit": "each"}, headers=h)
    _pass()  # recovered (and a purchase)
    client.post("/api/inventory/remove", json={"item_id": rice, "location_id": loc, "quantity": 8, "unit": "each"}, headers=h)
    _pass()
    lows = [e for e in _outbox() if e["type"] == "pantry.stock_low"]
    assert len(lows) == 1


# --- delivery ------------------------------------------------------------------


class _FakeKeystone:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers})
        outcome = self.responses.pop(0) if self.responses else 201
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, text="nope" if outcome >= 400 else "{}")


def _queue_two_events(client: httpx.Client, h: dict) -> None:
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    item = client.post("/api/items", json={"name": "Apple"}, headers=h).json()["id"]
    _connect(client, h)
    client.post("/api/inventory/add", json={"item_id": item, "location_id": loc, "quantity": 5, "unit": "each"}, headers=h)
    client.post(
        "/api/consumption/plate",
        json={"item_id": item, "location_id": loc, "quantity": 1, "unit": "each"},
        headers={**h, "X-Household-Profile": profile},
    )
    assert _pass() == 2


def test_delivery_posts_to_the_intake_with_the_publisher_token(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _queue_two_events(client, h)
    keystone = _FakeKeystone(201, 200)
    with SessionLocal() as db:
        counts = deliver_pending(db, poster=keystone)
    assert counts == {"delivered": 2, "rejected": 0, "deferred": 0}
    assert keystone.calls[0]["url"] == "https://wildstone.test/api/v1/household/events"
    assert keystone.calls[0]["headers"]["Authorization"] == "Bearer pub-secret"
    assert all(e["status"] == "delivered" for e in _outbox())

    status = client.get(f"{BASE}/connection", headers=h).json()
    assert status["relay"]["delivered"] == 2 and status["relay"]["pending"] == 0


def test_rejected_is_kept_and_outage_stops_the_batch(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _queue_two_events(client, h)

    with SessionLocal() as db:
        counts = deliver_pending(db, poster=_FakeKeystone(422))
    # First refused for good; the second went through on the default 201.
    assert counts["rejected"] == 1 and counts["delivered"] == 1
    assert [e["status"] for e in _outbox()] == ["rejected", "delivered"]


def test_outage_defers_with_backoff(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _queue_two_events(client, h)
    keystone = _FakeKeystone(httpx.ConnectError("down"))
    now = datetime.now(UTC)
    with SessionLocal() as db:
        counts = deliver_pending(db, poster=keystone, now=now)
    assert counts == {"delivered": 0, "rejected": 0, "deferred": 1}
    assert len(keystone.calls) == 1  # stopped: the next would fail the same way
    assert [e["status"] for e in _outbox()] == ["pending", "pending"]

    # Not due yet: the first row waits out its backoff and nothing overtakes it.
    early = _FakeKeystone()
    with SessionLocal() as db:
        deliver_pending(db, poster=early, now=now + timedelta(seconds=1))
    assert early.calls == []

    # Once due, both go, oldest first.
    later = _FakeKeystone()
    with SessionLocal() as db:
        assert deliver_pending(db, poster=later, now=now + timedelta(seconds=10))["delivered"] == 2
    assert [c["json"]["type"] for c in later.calls] == [e["type"] for e in _outbox()]


@pytest.mark.parametrize("code", [401, 503])
def test_auth_and_server_errors_are_retried_not_rejected(client: httpx.Client, code: int) -> None:
    h = _admin_headers(client)
    _queue_two_events(client, h)
    with SessionLocal() as db:
        counts = deliver_pending(db, poster=_FakeKeystone(code))
    assert counts["deferred"] == 1 and counts["rejected"] == 0
    assert _outbox()[0]["status"] == "pending"


def test_managed_install_connects_from_the_environment(client: httpx.Client, monkeypatch) -> None:
    """ADR-W042 A6: when Wildstone installs PantryOS it sets the address and
    token in the environment; the relay starts on its own, from "now"."""
    from app.core.config import settings
    from modules.wildstone.services.connection import ensure_started, get_state

    monkeypatch.setattr(settings, "wildstone_keystone_url", "https://keystone.wildstone")
    monkeypatch.setattr(settings, "wildstone_publisher_token", "from-env")
    h = _admin_headers(client)
    status = client.get(f"{BASE}/connection", headers=h).json()
    assert status["connected"] is True and status["keystone_url"] == "https://keystone.wildstone"

    with SessionLocal() as db:
        assert get_state(db) is None
        ensure_started(db)
        assert get_state(db) is not None


# --- review fixes -----------------------------------------------------------------


def _receipt_line(item_id: str, loc: str, receipt: str) -> None:
    """One line of an imported receipt, written the way the importer writes it."""
    from decimal import Decimal
    from uuid import UUID

    from modules.inventory.services.inventory import add_inventory

    with SessionLocal() as db:
        add_inventory(
            db, item_id=UUID(item_id), location_id=UUID(loc), quantity=Decimal("1"), unit="each",
            expiration_date=None, reason="purchase_import", note=f"purchase:{receipt}",
        )


def test_purchases_are_grouped_by_receipt_not_by_relay_pass(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    items = [client.post("/api/items", json={"name": n}, headers=h).json()["id"] for n in ("Milk", "Bread", "Jam")]
    _connect(client, h)
    _pass()

    # Two receipts and one item added by hand, all between two passes.
    _receipt_line(items[0], loc, "A")
    _receipt_line(items[1], loc, "A")
    _receipt_line(items[2], loc, "B")
    client.post("/api/inventory/add", json={"item_id": items[0], "location_id": loc, "quantity": 1, "unit": "each"}, headers=h)

    # While receipt lines are still arriving, the pass waits for them.
    assert _pass(receipt_quiet=timedelta(minutes=5)) == 0
    assert _pass() == 3
    purchases = [e["envelope"]["payload"] for e in _outbox() if e["type"] == "pantry.purchase_recorded"]
    assert sorted(p["item_count"] for p in purchases) == [1, 1, 2]
    assert sum(1 for p in purchases if p.get("from_receipt")) == 2


def test_a_receipt_running_off_a_full_page_is_read_whole(client: httpx.Client, monkeypatch) -> None:
    from modules.wildstone.services import relay

    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    milk = client.post("/api/items", json={"name": "Milk"}, headers=h).json()["id"]
    _connect(client, h)
    _pass()
    monkeypatch.setattr(relay, "LEDGER_PAGE", 3)
    client.post("/api/inventory/add", json={"item_id": milk, "location_id": loc, "quantity": 1, "unit": "each"}, headers=h)
    for _ in range(3):
        _receipt_line(milk, loc, "C")

    assert _pass() == 1  # the hand-added milk; the receipt waits for the next page
    assert _pass() == 1
    receipt = [e for e in _outbox() if e["envelope"]["payload"].get("from_receipt")]
    assert len(receipt) == 1 and receipt[0]["envelope"]["payload"]["item_count"] == 3


def test_stock_low_twice_without_a_stock_change_is_two_events(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    rice = client.post("/api/items", json={"name": "Rice"}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": rice, "location_id": loc, "quantity": 4, "unit": "each"}, headers=h)
    _connect(client, h)
    _pass()

    for threshold in (10, 1, 10):  # low, recovered, low — the ledger never moves
        resp = client.put(f"/api/items/{rice}", json={"restock_threshold": threshold}, headers=h)
        assert resp.status_code == 200, resp.text
        _pass()
    assert sum(1 for e in _outbox() if e["type"] == "pantry.stock_low") == 2


def test_pointing_at_a_different_wildstone_drops_the_old_queue(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _queue_two_events(client, h)
    assert [e["status"] for e in _outbox()] == ["pending", "pending"]

    # Only the token changes: same household, the queue stays.
    client.put(f"{BASE}/connection", json={"keystone_url": "https://wildstone.test", "publisher_token": "rotated"}, headers=h)
    assert len(_outbox()) == 2

    # A different Keystone needs its own token: the old one would be refused.
    no_token = client.put(f"{BASE}/connection", json={"keystone_url": "https://other-wildstone.test"}, headers=h)
    assert no_token.status_code == 400
    assert len(_outbox()) == 2

    # A different Keystone: nothing queued for the old one may reach it.
    moved = client.put(
        f"{BASE}/connection", json={"keystone_url": "https://other-wildstone.test", "publisher_token": "other"}, headers=h
    )
    assert moved.status_code == 200
    assert _outbox() == []
    assert moved.json()["relay"]["ledger_seq"] is not None


def test_news_already_sent_to_the_old_wildstone_reaches_the_new_one(client: httpx.Client) -> None:
    """Delivered ids from the old household must not dedupe away the same
    news for the new one (an expiring lot has the same id in both)."""
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    milk = client.post("/api/items", json={"name": "Milk"}, headers=h).json()["id"]
    _connect(client, h)
    soon = (date.today() + timedelta(days=2)).isoformat()
    client.post(
        "/api/inventory/add",
        json={"item_id": milk, "location_id": loc, "quantity": 1, "unit": "each", "expiration_date": soon},
        headers=h,
    ).raise_for_status()
    _pass()
    with SessionLocal() as db:
        db.query(WildstoneOutbox).update({"status": "delivered"})
        db.commit()
    assert [e["type"] for e in _outbox()].count("pantry.item_expiring") == 1

    client.put(
        f"{BASE}/connection", json={"keystone_url": "https://other-wildstone.test", "publisher_token": "other"}, headers=h
    ).raise_for_status()
    _pass()
    assert [(e["type"], e["status"]) for e in _outbox() if e["type"] == "pantry.item_expiring"] == [
        ("pantry.item_expiring", "pending")
    ]


# --- pairing (#77) -------------------------------------------------------------


def _exchange(client: httpx.Client, code: str, **overrides) -> httpx.Response:
    body = {"code": code, "keystone_url": "https://keystone.wildstone.test", "publisher_token": "minted-by-keystone"}
    body.update(overrides)
    return client.post(f"{BASE}/pairing/exchange", json=body)


def test_pairing_connects_both_ways_with_one_code(client: httpx.Client) -> None:
    from app.core.ratelimit import login_limiter

    login_limiter._failures.clear()
    h = _admin_headers(client)
    minted = client.post(f"{BASE}/pairing", headers=h)
    assert minted.status_code == 200, minted.text
    code = minted.json()["code"]
    assert minted.json()["expires_in"] == 300
    assert len(code) == 9 and code[4] == "-"

    # Typed with a stray space and in lower case: still the same code.
    done = _exchange(client, " " + code.lower())
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["contract"] == "pantry-1"
    agent = {"X-Agent-Key": body["agent_key"]}

    # The key Wildstone received works on the contract, reads and writes.
    assert client.get(f"{V1}/capabilities", headers=agent).status_code == 200
    assert client.post(f"{V1}/act/pantry.add", json={"list": "groceries", "item": "milk"}, headers=agent).status_code == 200
    # And PantryOS is now connected to that Keystone with that token.
    status = client.get(f"{BASE}/connection", headers=h).json()
    assert status["connected"] and status["keystone_url"] == "https://keystone.wildstone.test"
    assert status["paired_at"]

    # The code worked once.
    assert _exchange(client, code).status_code == 401


def test_pairing_tells_wildstone_the_web_address_only_when_configured(client: httpx.Client, monkeypatch) -> None:
    from app.core.config import settings
    from app.core.ratelimit import login_limiter

    login_limiter._failures.clear()
    h = _admin_headers(client)

    # Unset: the key is absent, not null, and nothing is guessed from the request.
    monkeypatch.setattr(settings, "pantryos_public_url", "")
    body = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()
    assert "web_url" not in body and body["contract"] == "pantry-1"

    # Configured: sent with the trailing slash trimmed, and shown to the admin.
    monkeypatch.setattr(settings, "pantryos_public_url", "https://pantry.home.example/")
    body = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()
    assert body["web_url"] == "https://pantry.home.example"
    status = client.get(f"{BASE}/connection", headers=h).json()
    assert (status["web_url"], status["web_url_problem"]) == ("https://pantry.home.example", None)

    # One Wildstone would ignore is not sent; the admin screen says why.
    monkeypatch.setattr(settings, "pantryos_public_url", "http://pantry.example.com")
    body = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()
    assert "web_url" not in body
    status = client.get(f"{BASE}/connection", headers=h).json()
    assert status["web_url"] is None and "https" in status["web_url_problem"]

    # And the response is typed in the published API.
    schema = client.get("/openapi.json").json()
    ref = schema["paths"][f"{BASE}/pairing/exchange"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    fields = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]
    assert set(fields) == {"agent_key", "contract", "web_url"}


@pytest.mark.parametrize(
    ("raw", "sent"),
    [
        ("https://pantry.example.com/", "https://pantry.example.com"),
        ("https://pantry.example.com:8443/app", "https://pantry.example.com:8443/app"),
        ("http://192.168.1.20:8080", "http://192.168.1.20:8080"),
        ("http://10.0.0.5", "http://10.0.0.5"),
        ("http://127.0.0.1:5173", "http://127.0.0.1:5173"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://169.254.1.1", "http://169.254.1.1"),
        ("http://100.100.1.1", "http://100.100.1.1"),
        ("http://[fe80::1]", "http://[fe80::1]"),
        ("http://[fd00::1]:8080", "http://[fd00::1]:8080"),
        ("http://pantry", "http://pantry"),
        ("http://pantry.local", "http://pantry.local"),
        ("http://Pantry.LAN", "http://Pantry.LAN"),
        ("http://pantry.internal", "http://pantry.internal"),
        ("http://pantry.tail1234.ts.net", "http://pantry.tail1234.ts.net"),
        ("", None),
        ("http://pantry.example.com", None),  # public name: https only
        ("http://8.8.8.8", None),
        ("http://100.128.0.1", None),  # just past the CGNAT range
        ("ftp://pantry.lan", None),
        ("pantry.lan", None),
        ("/pantry", None),
        ("https://user:pw@pantry.example.com", None),
        ("https://pantry.example.com/?x=1", None),
        ("https://pantry.example.com/#top", None),
        ("https://pantry.example.com:notaport", None),
        ("https://bad host", None),
        ("https://pantry.exa mple.com", None),
        ("https://pantry_1.example.com", None),
        ("https://-pantry.example.com", None),
        ("https://pantry..example.com", None),
        ("https://pantry.example.com/a b", None),
        ("http://pan\ttry.lan", None),
        ("https://pantry.example.com/" + "a" * 520, None),
    ],
)
def test_the_web_address_follows_wildstones_rules(raw: str, sent: str | None) -> None:
    from modules.wildstone.services import web_url

    url, problem = web_url.check(raw)
    assert url == sent
    assert (problem is None) == (sent is not None or raw == "")


def test_pairing_again_rotates_the_key(client: httpx.Client) -> None:
    from app.core.ratelimit import login_limiter

    login_limiter._failures.clear()
    h = _admin_headers(client)
    first = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()["agent_key"]
    second = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()["agent_key"]
    assert first != second
    assert client.get(f"{V1}/capabilities", headers={"X-Agent-Key": first}).status_code == 401
    assert client.get(f"{V1}/capabilities", headers={"X-Agent-Key": second}).status_code == 200


def test_pairing_refusals(client: httpx.Client, monkeypatch) -> None:
    from app.core.ratelimit import login_limiter
    from modules.wildstone.services import pairing

    login_limiter._failures.clear()
    h = _admin_headers(client)
    assert _exchange(client, "ABCD-EFGH").status_code == 401  # never minted

    code = client.post(f"{BASE}/pairing", headers=h).json()["code"]
    # A malformed request is refused WITHOUT spending the code.
    assert _exchange(client, code, keystone_url="not a url").status_code == 400
    assert _exchange(client, code, publisher_token=" ").status_code == 400
    # Asking for a new code replaces the old one.
    newer = client.post(f"{BASE}/pairing", headers=h).json()["code"]
    assert _exchange(client, code).status_code == 401

    # Expired.
    real_monotonic = pairing.time.monotonic
    monkeypatch.setattr(pairing.time, "monotonic", lambda: real_monotonic() + pairing.PAIRING_TTL_SECONDS + 1)
    assert _exchange(client, newer).status_code == 401
    monkeypatch.undo()

    # Minting a code needs a real admin login; an agent key is not one.
    assert client.post(f"{BASE}/pairing").status_code == 401

    # Repeated wrong codes are throttled like logins.
    login_limiter._failures.clear()
    for _ in range(login_limiter.max_failures):
        _exchange(client, "WXYZ-2345")
    assert _exchange(client, "WXYZ-2345").status_code == 429
    login_limiter._failures.clear()


# --- cook-along steps and timers (ADR 0026 §6.1) -------------------------------


def test_an_agent_ticks_steps_and_sets_timers_through_the_contract(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)
    recipe = client.post(
        "/api/recipes",
        json={"name": "Pasta", "servings": 2, "instructions": "1. Boil the water.\n2. Cook the pasta 9 minutes.\n3. Drain."},
        headers=h,
    ).json()
    caps = {c["name"]: c for c in client.get(f"{V1}/capabilities", headers=agent).json()["capabilities"]}
    assert caps["pantry.timer.set"]["proposedRisk"] == "read"
    assert caps["pantry.timers"]["kind"] == "query" and caps["pantry.timer_finished"]["kind"] == "event"

    assert client.get(f"{V1}/query/pantry.timers", headers=agent).json()["status"] == "empty"
    client.post(f"{V1}/act/pantry.cook.start", json={"recipe_id": recipe["id"]}, headers=agent)
    done = client.post(f"{V1}/act/pantry.cook.step", json={"action": "complete"}, headers=agent).json()["data"]["session"]
    assert done["completed_steps"] == [1] and done["step_number"] == 2
    assert done["step_timers"] == [{"seconds": 540, "label": "9 minutes"}]

    # "Set a timer for the pasta": tied to the cook and the step it is on.
    timer = client.post(f"{V1}/act/pantry.timer.set", json={"seconds": 540, "label": "Pasta"}, headers=agent).json()
    assert timer["data"]["timer"]["step_number"] == 2 and timer["data"]["timer"]["session_id"] == done["id"]
    listed = client.get(f"{V1}/query/pantry.timers", headers=agent).json()
    assert [t["label"] for t in listed["data"]["timers"]] == ["Pasta"]

    # Cancel by name; an unknown name is a 422, never a silent success.
    assert client.post(f"{V1}/act/pantry.timer.cancel", json={"label": "rice"}, headers=agent).status_code == 422
    stopped = client.post(f"{V1}/act/pantry.timer.cancel", json={"label": "pasta"}, headers=agent).json()
    assert stopped["data"]["timer"]["stopped"] == "cancelled"
    assert client.post(f"{V1}/act/pantry.timer.set", json={"seconds": 0}, headers=agent).status_code == 422


def test_a_timer_that_went_off_is_one_event(client: httpx.Client) -> None:
    from modules.planning.services import cook_timers

    h = _admin_headers(client)
    _connect(client, h)
    with SessionLocal() as db:
        cook_timers.start_timer(db, seconds=60, label="Eggs")
        cancelled = cook_timers.start_timer(db, seconds=60, label="Toast")
        cook_timers.stop_timer(db, timer_id=cancelled.id)

    later = datetime.now(UTC) + timedelta(minutes=2)
    assert _pass(now=later) >= 1
    events = [e for e in _outbox() if e["type"] == "pantry.timer_finished"]
    assert len(events) == 1
    assert events[0]["envelope"]["payload"]["label"] == "Eggs"
    # Read again, a minute later: nothing new.
    _pass(now=later + timedelta(minutes=1))
    assert len([e for e in _outbox() if e["type"] == "pantry.timer_finished"]) == 1


def test_a_finished_timer_names_whoever_set_it(client: httpx.Client) -> None:
    """The agent's member, or the member linked to the tablet's profile; a
    timer set by nobody in particular has no subject, never a guessed one."""
    h = _admin_headers(client)
    agent = _agent(client, h)
    _connect(client, h)
    profile = client.post("/api/household/profiles", json={"display_name": "Kim"}, headers=h).json()["id"]
    assert client.put(f"{BASE}/members/{profile}", json={"member": "kim"}, headers=h).status_code == 200

    by_agent = client.post(f"{V1}/act/pantry.timer.set", json={"seconds": 60, "label": "Pasta", "member": "sam"}, headers=agent)
    assert by_agent.status_code == 200, by_agent.text
    by_tablet = client.post("/api/cook-timers", json={"seconds": 60, "label": "Rice", "profile_id": profile}, headers=h)
    assert by_tablet.status_code == 201, by_tablet.text
    client.post("/api/cook-timers", json={"seconds": 60, "label": "Eggs"}, headers=h)
    # A member name Keystone would refuse is not passed on.
    client.post(f"{V1}/act/pantry.timer.set", json={"seconds": 60, "label": "Tea", "member": "not a name!"}, headers=agent)

    _pass(now=datetime.now(UTC) + timedelta(minutes=2))
    subjects = {
        e["envelope"]["payload"]["label"]: e["envelope"].get("subject")
        for e in _outbox()
        if e["type"] == "pantry.timer_finished"
    }
    assert subjects == {"Pasta": "sam", "Rice": "kim", "Eggs": None, "Tea": None}


def test_a_purchase_then_a_meal_are_queued_in_that_order(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    eggs = client.post("/api/items", json={"name": "Eggs"}, headers=h).json()["id"]
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    _connect(client, h)
    _pass()

    client.post("/api/inventory/add", json={"item_id": eggs, "location_id": loc, "quantity": 6, "unit": "each"}, headers=h)
    client.post(
        "/api/consumption/plate",
        json={"item_id": eggs, "location_id": loc, "quantity": 2, "unit": "each", "meal_type": "breakfast"},
        headers={**h, "X-Household-Profile": profile},
    ).raise_for_status()
    _pass()
    ledger = [e["type"] for e in _outbox() if e["type"] in ("pantry.purchase_recorded", "pantry.meal_logged")]
    assert ledger == ["pantry.purchase_recorded", "pantry.meal_logged"]


def test_a_row_that_committed_behind_the_cursor_is_still_sent(client: httpx.Client) -> None:
    """Sequence numbers are taken before commit, so a slow transaction's row can
    show up below a cursor that already moved past it. It is still sent — once."""
    from modules.inventory.services.feed import head_seq
    from modules.wildstone.services.connection import get_state

    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    eggs = client.post("/api/items", json={"name": "Eggs"}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": eggs, "location_id": loc, "quantity": 6, "unit": "each"}, headers=h)
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    _connect(client, h)
    _pass()

    client.post(
        "/api/consumption/plate",
        json={"item_id": eggs, "location_id": loc, "quantity": 2, "unit": "each", "meal_type": "lunch"},
        headers={**h, "X-Household-Profile": profile},
    ).raise_for_status()
    # The cursor stepped over the meal: a later row was read first.
    with SessionLocal() as db:
        state = get_state(db)
        state.ledger_seq = head_seq(db)
        db.commit()

    assert _pass() == 1
    assert [e["type"] for e in _outbox()].count("pantry.meal_logged") == 1
    assert _pass() == 0  # re-read again, but not sent twice


def test_expiring_food_says_where_it_is(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)
    fridge = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]
    freezer = client.post("/api/locations", json={"name": "Freezer"}, headers=h).json()["id"]
    milk = client.post("/api/items", json={"name": "Milk"}, headers=h).json()["id"]
    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=5)).isoformat()
    for loc, when in ((fridge, soon), (freezer, later)):
        client.post(
            "/api/inventory/add",
            json={"item_id": milk, "location_id": loc, "quantity": 1, "unit": "each", "expiration_date": when},
            headers=h,
        ).raise_for_status()

    items = client.get(f"{V1}/query/pantry.expiring", headers=agent).json()["data"]["items"]
    assert sorted((i["location"], i["days_left"]) for i in items) == [("Freezer", 5), ("Fridge", 2)]


def test_disconnect_sticks_for_a_connection_from_the_environment(client: httpx.Client, monkeypatch) -> None:
    """Clearing the saved settings cannot reach the environment, so Disconnect
    must still stop a managed install's relay; connecting again restarts it."""
    from app.core.config import settings
    from modules.wildstone.services.connection import ensure_started, get_state

    monkeypatch.setattr(settings, "wildstone_keystone_url", "https://keystone.wildstone")
    monkeypatch.setattr(settings, "wildstone_publisher_token", "from-env")
    h = _admin_headers(client)
    assert client.get(f"{BASE}/connection", headers=h).json()["connected"] is True

    client.delete(f"{BASE}/connection", headers=h).raise_for_status()
    assert client.get(f"{BASE}/connection", headers=h).json()["connected"] is False
    with SessionLocal() as db:
        ensure_started(db)
        assert get_state(db) is None  # the runner does not bring it back
    assert _pass() == 0

    _connect(client, h)
    assert client.get(f"{BASE}/connection", headers=h).json()["connected"] is True


def test_one_pass_queues_every_source_in_time_order(client: httpx.Client) -> None:
    """A recipe cooked, then a meal logged, before the next pass: the cook is
    queued first, though the ledger is read before cook records."""
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    flour = client.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()["id"]
    eggs = client.post("/api/items", json={"name": "Eggs"}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": flour, "location_id": loc, "quantity": 100, "unit": "g"}, headers=h)
    client.post("/api/inventory/add", json={"item_id": eggs, "location_id": loc, "quantity": 6, "unit": "each"}, headers=h)
    recipe = client.post(
        "/api/recipes",
        json={"name": "Flatbread", "servings": 1, "ingredients": [{"name": "Flour", "quantity": 8, "unit": "g"}]},
        headers=h,
    ).json()["id"]
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    _connect(client, h)
    _pass()

    sid = client.post("/api/cook-sessions", json={"recipe_id": recipe}, headers=h).json()["id"]
    client.post(f"/api/cook-sessions/{sid}/finish", json={"location_id": loc}, headers=h).raise_for_status()
    time.sleep(1.1)  # SQLite keeps whole seconds; Postgres would tell them apart anyway
    client.post(
        "/api/consumption/plate",
        json={"item_id": eggs, "location_id": loc, "quantity": 2, "unit": "each", "meal_type": "lunch"},
        headers={**h, "X-Household-Profile": profile},
    ).raise_for_status()
    _pass()

    kinds = [e["type"] for e in _outbox() if e["type"] in ("pantry.recipe_cooked", "pantry.meal_logged")]
    assert kinds == ["pantry.recipe_cooked", "pantry.meal_logged"]


def test_a_target_change_that_cannot_be_saved_keeps_the_old_queue(client: httpx.Client, monkeypatch) -> None:
    """If the new connection can't be stored (no encryption key, say), nothing
    queued for the current Wildstone may have been thrown away."""
    from modules.shopping.services import connector_config

    h = _admin_headers(client)
    _queue_two_events(client, h)
    before = _outbox()

    def no_key(*_args, **_kwargs):
        raise RuntimeError("CONFIG_SECRET_KEY is not set")

    monkeypatch.setattr(connector_config, "encrypt_json", no_key)
    failed = client.put(
        f"{BASE}/connection", json={"keystone_url": "https://other-wildstone.test", "publisher_token": "other"}, headers=h
    )
    assert failed.status_code >= 400
    assert _outbox() == before


def test_a_re_pair_that_cannot_connect_keeps_the_working_pairing(client: httpx.Client, monkeypatch) -> None:
    """Rotate only once the new connection is saved: a re-pair that fails there
    (no encryption key, say) leaves the current agent key working."""
    from app.core.ratelimit import login_limiter
    from modules.shopping.services import connector_config

    login_limiter._failures.clear()
    h = _admin_headers(client)
    first = _exchange(client, client.post(f"{BASE}/pairing", headers=h).json()["code"]).json()
    working = {"X-Agent-Key": first["agent_key"]}
    assert client.get(f"{V1}/capabilities", headers=working).status_code == 200

    code = client.post(f"{BASE}/pairing", headers=h).json()["code"]

    def no_key(*_args, **_kwargs):
        raise RuntimeError("CONFIG_SECRET_KEY is not set")

    monkeypatch.setattr(connector_config, "encrypt_json", no_key)
    failed = _exchange(client, code, keystone_url="https://other-keystone.test", publisher_token="other")
    assert failed.status_code >= 400
    assert client.get(f"{V1}/capabilities", headers=working).status_code == 200


def test_a_late_receipt_line_waits_for_the_rest_of_its_receipt(client: httpx.Client) -> None:
    """#81: a receipt line that committed behind the cursor, while the rest of
    its receipt is still arriving, is held with it — then the receipt goes out
    once, whole, not as a partial purchase whose later lines are dropped."""
    from modules.inventory.services.feed import head_seq
    from modules.wildstone.services.connection import get_state

    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    milk, bread = (client.post("/api/items", json={"name": n}, headers=h).json()["id"] for n in ("Milk", "Bread"))
    _connect(client, h)
    _pass()

    _receipt_line(milk, loc, "R1")
    # The cursor stepped over that line (it committed late)...
    with SessionLocal() as db:
        state = get_state(db)
        state.ledger_seq = head_seq(db)
        db.commit()
    # ...and the receipt's next line is still arriving.
    _receipt_line(bread, loc, "R1")

    assert _pass(receipt_quiet=timedelta(minutes=5)) == 0
    assert [e for e in _outbox() if e["type"] == "pantry.purchase_recorded"] == []

    # The receipt went quiet: one purchase, both lines.
    _pass()
    purchases = [e["envelope"]["payload"] for e in _outbox() if e["type"] == "pantry.purchase_recorded"]
    assert [p["item_count"] for p in purchases] == [2]


# --- #78: spoiled food and grocery-list changes --------------------------------


def test_spoiled_food_is_one_event(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]
    milk = client.post("/api/items", json={"name": "Milk"}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": milk, "location_id": loc, "quantity": 1, "unit": "each"}, headers=h)
    _connect(client, h)
    _pass()

    lot = client.get(f"/api/inventory/lots?item_id={milk}&location_id={loc}", headers=h).json()["lots"][0]["lot_id"]
    assert client.post(f"/api/inventory/lots/{lot}/spoil", headers=h).status_code == 200
    _pass()
    spoiled = [e["envelope"] for e in _outbox() if e["type"] == "pantry.item_spoiled"]
    assert len(spoiled) == 1
    assert spoiled[0]["payload"] == {"item": "Milk", "quantity": 1.0, "unit": "each"}
    assert _pass() == 0  # read again: nothing new


def test_a_burst_of_grocery_list_edits_is_one_change(client: httpx.Client) -> None:
    h = _admin_headers(client)
    client.post("/api/shopping-list", json={"name": "Milk"}, headers=h)
    _connect(client, h)
    # What was on the list when connecting is the starting picture, not news.
    assert _pass(list_quiet=timedelta(0)) == 0

    for name in ("Eggs", "Bread", "Jam"):
        assert client.post("/api/shopping-list", json={"name": name}, headers=h).status_code in (200, 201)
    # Still being edited: nothing yet.
    assert _pass() == 0
    # Settled: one event, with how many things are on the list.
    _pass(list_quiet=timedelta(0))
    changes = [e["envelope"] for e in _outbox() if e["type"] == "pantry.shopping_list_changed"]
    assert [c["payload"]["item_count"] for c in changes] == [4]
    assert _pass(list_quiet=timedelta(0)) == 0  # read again: nothing new

    # A later change is a new event.
    entry = client.get("/api/shopping-list", headers=h).json()
    entries = entry["items"] if isinstance(entry, dict) else entry
    first = entries[0]["id"]
    client.patch(f"/api/shopping-list/{first}", json={"status": "purchased"}, headers=h)
    _pass(list_quiet=timedelta(0))
    changes = [e["envelope"] for e in _outbox() if e["type"] == "pantry.shopping_list_changed"]
    assert [c["payload"]["item_count"] for c in changes] == [4, 3]
    assert len({c["eventId"] for c in changes}) == 2


def test_running_low_changes_the_list_without_anyone_opening_it(client: httpx.Client) -> None:
    h = _admin_headers(client)
    loc = client.post("/api/locations", json={"name": "Kitchen"}, headers=h).json()["id"]
    rice = client.post("/api/items", json={"name": "Rice", "restock_threshold": 3}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": rice, "location_id": loc, "quantity": 5, "unit": "each"}, headers=h)
    _connect(client, h)
    assert _pass(list_quiet=timedelta(0)) == 0

    # Rice drops below 3; nobody reads the grocery list.
    client.post("/api/inventory/remove", json={"item_id": rice, "location_id": loc, "quantity": 4, "unit": "each"}, headers=h)
    _pass(list_quiet=timedelta(0))
    changes = [e["envelope"] for e in _outbox() if e["type"] == "pantry.shopping_list_changed"]
    assert [c["payload"]["item_count"] for c in changes] == [1]

    # Reading the list afterwards finds it already up to date: nothing new.
    names = [e["name"] for e in client.get("/api/shopping-list", headers=h).json()["items"]]
    assert names == ["Rice"]
    _pass(list_quiet=timedelta(0))
    assert len([e for e in _outbox() if e["type"] == "pantry.shopping_list_changed"]) == 1


# --- #79: meal logging by food name -------------------------------------------


def _meal_setup(client: httpx.Client):
    h = _admin_headers(client)
    agent = _agent(client, h)
    loc = client.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]
    eggs = client.post("/api/items", json={"name": "Eggs"}, headers=h).json()["id"]
    client.post("/api/inventory/add", json={"item_id": eggs, "location_id": loc, "quantity": 6, "unit": "each"}, headers=h)
    profile = client.post("/api/household/profiles", json={"display_name": "Sam"}, headers=h).json()["id"]
    client.put(f"{BASE}/members/{profile}", json={"member": "sam"}, headers=h).raise_for_status()
    return h, agent, loc, eggs


def _eggs_left(client, h, loc, eggs) -> float:
    rows = client.get(f"/api/inventory?location_id={loc}", headers=h).json()["items"]
    return float(next(r["quantity"] for r in rows if r["item_id"] == eggs))


def test_log_two_eggs_for_sams_breakfast_and_undo_it(client: httpx.Client) -> None:
    h, agent, loc, eggs = _meal_setup(client)
    logged = client.post(
        f"{V1}/act/pantry.meal.log",
        json={"member": "sam", "food": "eggs", "quantity": 2, "meal_type": "breakfast"},
        headers=agent,
    )
    assert logged.status_code == 200, logged.text
    meal = logged.json()["data"]["meal"]
    assert (meal["food"], meal["quantity"], meal["from_stock"]) == ("Eggs", 2.0, True)
    assert _eggs_left(client, h, loc, eggs) == 4

    undo = client.post(f"{V1}/act/pantry.meal.undo", json={"event_id": meal["event_id"]}, headers=agent).json()
    assert undo["data"] == {"undone": meal["event_id"], "already_undone": False}
    assert _eggs_left(client, h, loc, eggs) == 6
    again = client.post(f"{V1}/act/pantry.meal.undo", json={"event_id": meal["event_id"]}, headers=agent).json()
    assert again["data"]["already_undone"] is True
    assert _eggs_left(client, h, loc, eggs) == 6


def test_an_uncertain_food_asks_instead_of_guessing(client: httpx.Client, monkeypatch) -> None:
    from modules.wildstone.services import meals

    h, agent, _loc, _eggs = _meal_setup(client)
    egg_noodles = client.post("/api/items", json={"name": "Egg noodles"}, headers=h).json()["id"]
    client.post("/api/items", json={"name": "Rice noodles"}, headers=h)

    # Two catalog foods fit: ask, naming both.
    unsure = client.post(f"{V1}/act/pantry.meal.log", json={"member": "sam", "food": "noodle"}, headers=agent)
    assert unsure.status_code == 422
    assert "Egg noodles" in unsure.text and "Rice noodles" in unsure.text and egg_noodles in unsure.text
    # Answered with the item_id: logged (nothing in stock, so not taken from it).
    chosen = client.post(
        f"{V1}/act/pantry.meal.log", json={"member": "sam", "food": "noodle", "item_id": egg_noodles}, headers=agent
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["data"]["meal"]["from_stock"] is False

    # Not a catalog food: USDA's candidates, to be sent back as an fdc_id.
    monkeypatch.setattr(
        meals,
        "search_food_by_name",
        lambda db, q, limit: {"candidates": [{"fdc_id": 173424, "description": "Egg, whole, hard-boiled"}]},
    )
    usda = client.post(f"{V1}/act/pantry.meal.log", json={"member": "sam", "food": "hard boiled egg"}, headers=agent)
    assert usda.status_code == 422 and "173424" in usda.text

    # Nobody linked to that member: said so, nothing logged.
    stranger = client.post(f"{V1}/act/pantry.meal.log", json={"member": "alex", "food": "eggs"}, headers=agent)
    assert stranger.status_code == 422 and "no PantryOS profile is linked" in stranger.text


def test_meal_actions_are_advertised_as_domestic(client: httpx.Client) -> None:
    h = _admin_headers(client)
    agent = _agent(client, h)
    caps = {c["name"]: c for c in client.get(f"{V1}/capabilities", headers=agent).json()["capabilities"]}
    assert caps["pantry.meal.log"]["proposedRisk"] == "domestic"
    assert caps["pantry.meal.undo"]["proposedRisk"] == "domestic"
    assert {"pantry.item_spoiled", "pantry.shopping_list_changed"} <= set(caps)


def test_two_products_of_one_ingredient_are_offered_not_guessed(client: httpx.Client) -> None:
    """Codex on #82: a canonical "same ingredient" match is not certain."""
    h, agent, _loc, _eggs = _meal_setup(client)
    # "Egg" and "Eggs." are both the ingredient "egg"; nothing is named "eggs"
    # exactly — the Eggs item from setup is archived so it isn't the answer.
    client.delete(f"/api/items/{_eggs}", headers=h)
    a = client.post("/api/items", json={"name": "Egg"}, headers=h).json()["id"]
    b = client.post("/api/items", json={"name": "Eggs."}, headers=h).json()["id"]
    unsure = client.post(f"{V1}/act/pantry.meal.log", json={"member": "sam", "food": "eggs"}, headers=agent)
    assert unsure.status_code == 422, unsure.text
    assert a in unsure.text and b in unsure.text


def test_an_unknown_profile_id_is_refused(client: httpx.Client) -> None:
    _h, agent, _loc, _eggs = _meal_setup(client)
    bad = client.post(
        f"{V1}/act/pantry.meal.log",
        json={"profile_id": "00000000-0000-0000-0000-000000000000", "food": "eggs"},
        headers=agent,
    )
    assert bad.status_code == 422 and "no active household profile" in bad.text


def test_clearing_the_list_and_repeating_a_change_are_both_news(client: httpx.Client) -> None:
    h = _admin_headers(client)
    _connect(client, h)
    _pass(list_quiet=timedelta(0))

    entry = client.post("/api/shopping-list", json={"name": "Milk"}, headers=h).json()["id"]
    _pass(list_quiet=timedelta(0))
    time.sleep(1.1)  # SQLite keeps whole seconds
    client.patch(f"/api/shopping-list/{entry}", json={"quantity": 2}, headers=h)
    _pass(list_quiet=timedelta(0))
    time.sleep(1.1)
    client.patch(f"/api/shopping-list/{entry}", json={"quantity": 1}, headers=h)
    _pass(list_quiet=timedelta(0))
    time.sleep(1.1)
    client.patch(f"/api/shopping-list/{entry}", json={"quantity": 2}, headers=h)
    _pass(list_quiet=timedelta(0))
    # The last entry deleted: the list is empty, and that is a change too —
    # even with the quiet period, since nothing more can arrive.
    assert client.delete(f"/api/shopping-list/{entry}", headers=h).status_code == 204
    _pass()

    counts = [e["envelope"]["payload"]["item_count"] for e in _outbox() if e["type"] == "pantry.shopping_list_changed"]
    assert counts == [1, 1, 1, 1, 0]
