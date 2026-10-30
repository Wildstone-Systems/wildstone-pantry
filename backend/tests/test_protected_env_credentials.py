"""Regression tests for the protected-env config guard (GH #13, replayed).

The guard must reject the default ``pantryos/pantryos`` database credentials in a
protected environment regardless of host — a host-specific allowlist would let
staging/production start on the committed compose defaults (``...@postgres...``).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

_SECURE_PASSWORD = "Str0ng!Passphrase"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://pantryos:pantryos@localhost:5432/pantryos",
        "postgresql+psycopg://pantryos:pantryos@postgres:5432/pantryos",
        "postgresql+psycopg://pantryos:pantryos@10.0.0.5:5432/pantryos",
    ],
)
def test_default_db_credentials_rejected_in_protected_env(database_url: str) -> None:
    with pytest.raises(ValueError, match="default development credentials"):
        Settings(
            environment="production",
            database_url=database_url,
            auth_personal_password=_SECURE_PASSWORD,
        )


def test_non_default_credentials_accepted_in_protected_env() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://appuser:s3cr3t@db:5432/pantryos",
        auth_personal_password=_SECURE_PASSWORD,
    )
    assert settings.environment == "production"


def test_default_db_credentials_allowed_in_development() -> None:
    settings = Settings(
        environment="development",
        database_url="postgresql+psycopg://pantryos:pantryos@postgres:5432/pantryos",
    )
    assert settings.environment == "development"
