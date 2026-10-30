"""Single-origin SPA serving from the backend (ADR 0018 §4).

With FRONTEND_DIST pointing at a built frontend, the API process serves the
UI: client-side routes fall back to index.html, real assets are served as-is,
and /api misses stay honest 404s (never HTML).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from app.core.config import settings
from app.core.spa import SpaStaticFiles
from app.main import create_app


def _make_dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>PantryOS</title>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('pantryos');")
    return tmp_path


def test_no_dist_configured_mounts_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "frontend_dist", "")
    client = TestClient(create_app())
    assert client.get("/nonexistent-page").status_code == 404
    assert client.get("/health").status_code == 200


def test_spa_serving_with_dist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "frontend_dist", str(_make_dist(tmp_path)))
    client = TestClient(create_app())

    # Root and client-side routes serve the app shell.
    root = client.get("/")
    assert root.status_code == 200
    assert "PantryOS" in root.text
    deep_link = client.get("/shopping-list")
    assert deep_link.status_code == 200
    assert "PantryOS" in deep_link.text

    # Real assets are served as themselves.
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text

    # API routes still win over the mount, and API misses stay 404 — a typo'd
    # endpoint must never come back as index.html.
    assert client.get("/health").status_code == 200
    missing_api = client.get("/api/definitely-not-a-route")
    assert missing_api.status_code == 404
    assert "text/html" not in missing_api.headers.get("content-type", "")


def test_missing_index_disables_the_mount(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "frontend_dist", str(tmp_path))  # empty dir
    client = TestClient(create_app())
    assert client.get("/").status_code == 404
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["api/missing-endpoint", "api\\missing-endpoint"])
def test_api_miss_never_returns_spa_for_either_path_separator(tmp_path, path):
    spa = SpaStaticFiles(directory=str(_make_dist(tmp_path)), html=True)
    # Starlette supplies platform-normalized filesystem paths to get_response.
    # Exercise both forms on every OS, with a real index available to fall back to.
    with pytest.raises(HTTPException) as error:
        asyncio.run(spa.get_response(path, {"type": "http", "method": "GET"}))
    assert error.value.status_code == 404
