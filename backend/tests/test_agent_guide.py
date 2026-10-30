"""The agent API guide is served and every path it lists is a real route."""

from __future__ import annotations

import re

import httpx

from app.main import create_app


def test_guide_is_served_ungated(client: httpx.Client) -> None:
    resp = client.get("/api/agent/guide")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auth"]["header"].startswith("X-Agent-Key")
    assert body["tasks"] and body["common_mistakes"]


def test_every_guide_path_resolves_to_a_real_route(client: httpx.Client) -> None:
    """Drift guard: a task pointing at a nonexistent path would be worse than none."""
    guide = client.get("/api/agent/guide").json()

    # Real routes, with {param} placeholders normalised to a common token.
    real = {re.sub(r"\{[^}]+\}", "{}", route.path) for route in create_app().routes if hasattr(route, "path")}

    for task in guide["tasks"]:
        norm = re.sub(r"\{[^}]+\}", "{}", task["path"])
        assert norm in real, f"guide task '{task['task']}' points at unknown path {task['path']}"
