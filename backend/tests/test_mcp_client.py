"""PantryOSClient (used by the MCP server) exercised against the in-process app.

Validates the client's request wiring + agent-key auth end-to-end without needing
the MCP runtime. Uses a real minted agent key and an httpx ASGI transport.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from mcp_server.client import PantryOSClient

_JSONLD = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"MCP Coffee",
 "gtin13":"0999999999999","offers":{"price":"9.99","priceCurrency":"USD"}}
</script></head><body></body></html>
"""


def _bootstrap_agent_key() -> str:
    """Mint an agent key via the app (bearer), return the plaintext secret."""
    c = TestClient(app)
    c.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    token = c.post(
        "/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"}
    ).json()["token"]
    return c.post(
        "/api/agent-keys", json={"name": "mcp-test"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["secret"]


def _client(agent_key: str) -> PantryOSClient:
    return PantryOSClient(agent_key=agent_key, http=TestClient(app))


def test_client_drives_shopping_and_ingest(monkeypatch) -> None:
    monkeypatch.setattr("modules.agent.services.product_url.fetch_html", lambda url: _JSONLD)
    client = _client(_bootstrap_agent_key())

    # Free-text shopping add via the client.
    need = client.add_to_shopping_list(name="Napkins")
    assert need["status"] == "pending"

    # Product-URL ingest routed through the client -> agent endpoint.
    result = client.ingest_product_url(url="https://shop.example.com/coffee")
    assert result["item_name"] == "MCP Coffee"
    assert result["barcode"] == "0999999999999"
    assert result["shopping_item_id"] is not None

    # Reads work too.
    listed = client.list_shopping(status="needed")
    names = {row["name"] for row in listed["items"]}
    assert {"Napkins", "MCP Coffee"} <= names

    assert "locations" in client.list_locations()


def test_client_summary_and_planning_reads() -> None:
    """The Phase-1 additions (ADR 0011): summary, recipes, purchases wiring."""
    client = _client(_bootstrap_agent_key())

    summary = client.get_summary(within_days=3)
    assert summary["within_days"] == 3
    assert {"locations", "expiring_soon", "expired", "low_stock_items", "shopping_pending"} <= set(summary)

    assert client.list_recipes() == []
    assert "suggestions" in client.suggest_recipes()
    assert "purchases" in client.list_purchases()


def _pulled_pork_setup(agent_key: str) -> tuple[PantryOSClient, str, str]:
    """A roast in the fridge and a recipe that turns 3 lb of it into pulled pork.

    Catalog/recipe writes go through the admin API — the MCP client deliberately
    exposes cooking and logging, not catalogue authoring.
    """
    c = TestClient(app)
    token = c.post(
        "/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"}
    ).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    pork = c.post("/api/items", json={"name": "Pork Butt Roast", "size_quantity": 5, "size_unit": "lb"}, headers=h).json()["id"]
    pulled = c.post("/api/items", json={"name": "Pulled Pork"}, headers=h).json()["id"]
    location = c.post("/api/locations", json={"name": "Fridge"}, headers=h).json()["id"]

    client = _client(agent_key)
    client.add_inventory(item_id=pork, location_id=location, quantity=5, unit="lb")

    recipe = c.post(
        "/api/recipes",
        json={
            "name": "Pulled Pork",
            "servings": 8,
            "output_item_id": pulled,
            "output_quantity": 60,
            "output_unit": "oz",
            "ingredients": [{"item_id": pork, "quantity_required": 3, "unit": "lb"}],
        },
        headers=h,
    ).json()["id"]
    return client, location, recipe


def test_client_cooks_with_a_weighed_and_portioned_yield() -> None:
    """The agent can record what a cook observed, not just that it happened
    (ADR 0025): a weighed dish and the tubs it was packed into."""
    client, location, recipe = _pulled_pork_setup(_bootstrap_agent_key())

    cooked = client.cook_recipe(
        recipe,
        location_id=location,
        output_weight_grams=1800,
        output_portions=3,
    )
    assert cooked["produced_portions"] == 3
    assert float(cooked["produced_portion_size"]) == 600.0
    assert float(cooked["produced_quantity"]) == 1800.0


def test_client_cook_without_the_new_fields_is_unchanged() -> None:
    """Omitting them must send no keys at all — a null portion count is not the
    same request as no portioning at all."""
    client, location, recipe = _pulled_pork_setup(_bootstrap_agent_key())

    cooked = client.cook_recipe(recipe, location_id=location)
    assert cooked.get("produced_portions") is None
    assert float(cooked["produced_quantity"]) == 60.0  # the declared yield


def test_client_rejects_bad_agent_key() -> None:
    client = _client("pk_not-a-real-key")
    try:
        client.list_shopping()
        raise AssertionError("expected an auth error")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 401


def test_client_drives_a_cook_along() -> None:
    """ADR 0026 §6: the agent can start, step through, and finish a recipe."""
    key = _bootstrap_agent_key()
    pantry = _client(key)
    c = TestClient(app)
    h = {"X-Agent-Key": key}
    loc = c.post("/api/locations", json={"name": "Pantry"}, headers=h).json()["id"]
    item = c.post("/api/items", json={"name": "Flour", "stock_mode": "measured", "size_unit": "g"}, headers=h).json()
    c.post("/api/inventory/add", json={"item_id": item["id"], "location_id": loc, "quantity": 50, "unit": "g"}, headers=h)
    recipe = c.post(
        "/api/recipes",
        json={"name": "Bread", "servings": 2, "instructions": "1. Knead.\n2. Bake.",
              "ingredients": [{"name": "Flour", "quantity": 10, "unit": "g"}]},
        headers=h,
    ).json()

    assert pantry.active_cook_session() is None
    assert pantry.start_cooking(recipe["id"])["step_text"] == "Knead."
    assert pantry.move_cook_step("next")["step_text"] == "Bake."
    assert pantry.active_cook_session()["step_number"] == 2
    done = pantry.finish_cooking(location_id=loc)
    assert done["session"]["status"] == "finished"
    assert pantry.active_cook_session() is None
    # Answered: a later finish with nothing cooking says so, rather than
    # replaying this cook as if it were a new one.
    with pytest.raises(RuntimeError, match="Nothing is being cooked"):
        pantry.finish_cooking(location_id=loc)

    # A finish whose answer is lost in transit is retried against the same cook
    # and gets it back, not an error — and nothing is taken twice.
    second = pantry.start_cooking(recipe["id"])
    real_post = pantry._post

    def lost_answer(path, payload):
        real_post(path, payload)
        raise httpx.ReadTimeout("the answer was lost")

    pantry._post = lost_answer
    with pytest.raises(httpx.ReadTimeout):
        pantry.finish_cooking(location_id=loc)
    pantry._post = real_post
    again = pantry.finish_cooking(location_id=loc)
    assert again["cook"]["already_finished"] is True
    assert again["session"]["id"] == second["id"]


def test_client_ticks_steps_and_runs_timers() -> None:
    """ADR 0026 §6.1: "done, what's next?", "set a timer for the pasta"."""
    key = _bootstrap_agent_key()
    pantry = _client(key)
    c = TestClient(app)
    h = {"X-Agent-Key": key}
    recipe = c.post(
        "/api/recipes",
        json={"name": "Pasta", "servings": 2, "instructions": "1. Boil water.\n2. Cook pasta 9 minutes.\n3. Drain."},
        headers=h,
    ).json()

    pantry.start_cooking(recipe["id"])
    step = pantry.complete_cook_step()
    assert step["completed_steps"] == [1] and step["step_number"] == 2 and step["steps_left"] == 2

    timer = pantry.set_timer(540, label="Pasta")
    assert timer["step_number"] == 2 and timer["session_id"] == step["id"]
    pantry.set_timer(60, label="Garlic bread")
    assert [t["label"] for t in pantry.list_timers()] == ["Garlic bread", "Pasta"]
    assert pantry.stop_timer(label="pasta")["stopped"] == "cancelled"
    assert [t["label"] for t in pantry.list_timers()] == ["Garlic bread"]
    try:
        pantry.stop_timer(label="rice")
    except RuntimeError as exc:
        assert "rice" in str(exc)
    else:
        raise AssertionError("stopping a timer that isn't running should say so")
    pantry.stop_cooking()
