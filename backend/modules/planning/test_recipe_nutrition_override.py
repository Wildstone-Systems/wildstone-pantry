"""Source-provided per-serving recipe nutrition override (issue #51).

Covers the API contract, validation bounds, the strict write-credential guard,
the MCP client/tool round trip, and the reversible migration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from mcp_server.client import PantryOSClient

_OVERRIDE = {"calories": 250.5, "protein_g": 12, "fat_g": 8.25, "carbs_g": 30, "source": "Publisher label"}


def _admin(client: httpx.Client) -> dict[str, str]:
    client.post("/api/auth/bootstrap-admin", json={"email": "admin@pantryos.local", "password": "TestPass123!"})
    token = client.post(
        "/api/auth/login", json={"email": "admin@pantryos.local", "password": "TestPass123!"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _agent_key(client, admin, scopes: str) -> str:
    resp = client.post("/api/agent-keys", json={"name": f"k-{scopes}", "scopes": scopes}, headers=admin)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["secret"]


def _partial_recipe(client, h, servings: int = 4) -> str:
    """A recipe whose ingredient rollup is INCOMPLETE (volume amount, no density)."""
    item = client.post("/api/items", json={"name": "Mystery Spice", "energy_kcal_100g": 250}, headers=h).json()["id"]
    recipe = client.post("/api/recipes", json={"name": "Imported Stew", "servings": servings}, headers=h).json()["id"]
    resp = client.put(
        f"/api/recipes/{recipe}/ingredients",
        json=[{"item_id": item, "quantity_required": 1, "unit": "tsp"}],
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    return recipe


def _url(recipe_id: str) -> str:
    return f"/api/recipes/{recipe_id}/nutrition-override"


def test_override_round_trip_and_clear_restores_derived(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h, servings=4)

    before = client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()
    assert before["complete"] is False and before["unresolved"]
    assert before["basis"] == "ingredient_derived" and before["provenance"] is None

    resp = client.put(_url(recipe), json=_OVERRIDE, headers=h)
    assert resp.status_code == 200, resp.text
    read = client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()
    assert read == resp.json()
    assert read["basis"] == "source_override"
    assert read["provenance"] == "Publisher label"
    assert read["complete"] is True and read["unresolved"] == []
    assert read["per_serving"] == {"calories": 250.5, "protein_g": 12.0, "fat_g": 8.25, "carbs_g": 30.0}
    assert read["total"] == {"calories": 1002.0, "protein_g": 48.0, "fat_g": 33.0, "carbs_g": 120.0}

    # Replace.
    replaced = client.put(_url(recipe), json={**_OVERRIDE, "calories": 300, "source": "  Site  "}, headers=h).json()
    assert replaced["per_serving"]["calories"] == 300.0 and replaced["provenance"] == "Site"

    cleared = client.delete(_url(recipe), headers=h)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == before


def test_override_404_for_unknown_recipe(client: httpx.Client) -> None:
    h = _admin(client)
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.put(_url(missing), json=_OVERRIDE, headers=h).status_code == 404
    assert client.delete(_url(missing), headers=h).status_code == 404


@pytest.mark.parametrize(
    "patch",
    [
        {"calories": 0},
        {"calories": -1},
        {"protein_g": -0.01},
        {"fat_g": "NaN"},
        {"carbs_g": "Infinity"},
        {"calories": 10000000},
        {"calories": 1e308},
        {"protein_g": 1.234},
        {"source": ""},
        {"source": "   "},
        {"source": "x" * 201},
        {"carbs_g": None},
        {"unexpected": 1},
    ],
)
def test_override_validation_returns_422(client: httpx.Client, patch: dict) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h)
    resp = client.put(_url(recipe), json={**_OVERRIDE, **patch}, headers=h)
    assert resp.status_code == 422, resp.text


def test_override_accepts_exact_upper_bounds(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h, servings=1)
    body = {"calories": 9999999.99, "protein_g": 0, "fat_g": 0, "carbs_g": 0, "source": "x" * 200}
    resp = client.put(_url(recipe), json=body, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["per_serving"]["calories"] == 9999999.99


def test_missing_override_field_is_rejected(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h)
    body = {k: v for k, v in _OVERRIDE.items() if k != "fat_g"}
    assert client.put(_url(recipe), json=body, headers=h).status_code == 422


def test_personal_mode_rejects_anonymous_and_bad_tokens(client: httpx.Client) -> None:
    # No admin bootstrapped → personal/kiosk mode, where most routes are open.
    recipe = _partial_recipe(client, {})
    for headers in ({}, {"Authorization": "Bearer not-a-real-token"}, {"Authorization": "garbage"}):
        assert client.put(_url(recipe), json=_OVERRIDE, headers=headers).status_code == 401
        assert client.delete(_url(recipe), headers=headers).status_code == 401


def test_write_guard_rejects_unauthorized_identities(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h)
    read_key = _agent_key(client, h, "read")
    write_key = _agent_key(client, h, "read,write")

    cases = {
        "anonymous": ({}, 401),
        "malformed": ({"Authorization": "Bearer"}, 401),
        "invalid token": ({"Authorization": "Bearer nope"}, 401),
        "invalid agent key": ({"X-Agent-Key": "pk_invalid"}, 401),
        "read-only agent": ({"X-Agent-Key": read_key}, 403),
    }
    from app.services.auth import create_user

    with SessionLocal() as db:
        create_user(db, email="member@example.com", password="MemberPass987!", role="member")
    member = client.post("/api/auth/login", json={"email": "member@example.com", "password": "MemberPass987!"})
    member_token = member.json()["token"]
    cases["member"] = ({"Authorization": "Bearer " + member_token}, 403)
    # A member can mint their own write-scoped agent key; it must not carry
    # more authority than the member does.
    member_key = client.post(
        "/api/agent-keys", json={"name": "member-key"}, headers={"Authorization": "Bearer " + member_token}
    )
    assert member_key.status_code == 200, member_key.text
    cases["member's write agent key"] = ({"X-Agent-Key": member_key.json()["secret"]}, 403)

    for name, (headers, expected) in cases.items():
        assert client.put(_url(recipe), json=_OVERRIDE, headers=headers).status_code == expected, name
        assert client.delete(_url(recipe), headers=headers).status_code == expected, name

    # Nothing above leaked through.
    assert client.get(f"/api/recipes/{recipe}/nutrition", headers=h).json()["basis"] == "ingredient_derived"
    assert client.put(_url(recipe), json=_OVERRIDE, headers={"X-Agent-Key": write_key}).status_code == 200


def test_mcp_client_round_trip_and_read_only_rejection(client: httpx.Client) -> None:
    h = _admin(client)
    recipe = _partial_recipe(client, h, servings=2)
    writer = PantryOSClient(agent_key=_agent_key(client, h, "read,write"), http=TestClient(app))
    reader = PantryOSClient(agent_key=_agent_key(client, h, "read"), http=TestClient(app))

    with pytest.raises(httpx.HTTPStatusError) as denied:
        reader.set_recipe_nutrition_override(recipe, **{k: v for k, v in _OVERRIDE.items()})
    assert denied.value.response.status_code == 403

    with pytest.raises(httpx.HTTPStatusError) as invalid:
        writer.set_recipe_nutrition_override(recipe, calories=0, protein_g=1, fat_g=1, carbs_g=1, source="s")
    assert invalid.value.response.status_code == 422

    written = writer.set_recipe_nutrition_override(recipe, **_OVERRIDE)
    assert written["basis"] == "source_override"
    read = reader.recipe_nutrition(recipe)
    assert read == written and read["total"]["calories"] == 501.0

    cleared = writer.clear_recipe_nutrition_override(recipe)
    assert cleared["basis"] == "ingredient_derived" and cleared["complete"] is False


def test_mcp_tool_schemas() -> None:
    # The MCP runtime lives in its own virtualenv (mcp_server/README.md); skip
    # where it isn't installed rather than fail the backend suite.
    pytest.importorskip("mcp.server.fastmcp")
    import anyio

    from mcp_server import server

    tools = {t.name: t for t in anyio.run(server.mcp.list_tools)}
    setter = tools["set_recipe_nutrition_override"].inputSchema
    assert set(setter["required"]) == {"recipe_id", "calories", "protein_g", "fat_g", "carbs_g", "source"}
    assert setter["properties"]["calories"]["type"] == "number"
    assert tools["clear_recipe_nutrition_override"].inputSchema["required"] == ["recipe_id"]
    assert "basis" in tools["recipe_nutrition"].description


def _load_migration():
    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0072_recipe_nutrition_override.py"
    spec = importlib.util.spec_from_file_location("mig_0072", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_upgrade_downgrade_preserves_recipes(tmp_path) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE recipes (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, servings INTEGER)"))
        conn.execute(sa.text("INSERT INTO recipes VALUES ('r1', 'Soup', 4)"))

    migration = _load_migration()
    cols = lambda conn: {c["name"] for c in sa.inspect(conn).get_columns("recipes")}  # noqa: E731
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert {"nutrition_override_calories", "nutrition_override_source"} <= cols(conn)
        assert conn.execute(sa.text("SELECT name, servings, nutrition_override_calories FROM recipes")).one() == ("Soup", 4, None)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert cols(conn) == {"id", "name", "servings"}
        assert conn.execute(sa.text("SELECT id, name, servings FROM recipes")).one() == ("r1", "Soup", 4)
