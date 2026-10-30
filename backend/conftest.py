"""Test harness: in-process ASGI app against an isolated SQLite database.

Tests run the real FastAPI app via an in-process transport (no live server, no
Postgres). The portable column types in ``app.core.db_types`` let the ORM build
the schema from ``Base.metadata`` on SQLite. Existing tests that call
``httpx.get``/``post``/... against an absolute URL keep working unchanged:
``_patch_httpx`` redirects the module-level httpx functions to a client whose
transport is the in-process app (``ASGITransport`` ignores the host and serves
the app directly), and ``reset_database`` recreates the schema between tests for
isolation.
"""

from __future__ import annotations

import base64
import os
import tempfile

# Configure the app for testing BEFORE it is imported (settings read env at import).
_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pantryos-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH}")
# A fixed, valid Fernet key so the connector-config encryption path is exercised
# in tests without checking in a key-like literal.
os.environ.setdefault("CONFIG_SECRET_KEY", base64.urlsafe_b64encode(bytes([1]) * 32).decode())

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

# A single in-process client bound to the ASGI app; all tests share it.
# TestClient is a sync httpx.Client whose transport serves the app directly,
# so absolute URLs (e.g. http://localhost:8000/...) still route to the app.
_client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    """Recreate a clean schema before each test for isolation."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    """Route module-level httpx calls through the in-process app."""
    for method in ("get", "post", "put", "delete", "patch", "request"):
        monkeypatch.setattr(httpx, method, getattr(_client, method))
    yield


@pytest.fixture
def client() -> httpx.Client:
    """In-process HTTP client bound to the app (for new-style tests)."""
    return _client
