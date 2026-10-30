"""Portable SQLAlchemy column types.

These render as native PostgreSQL types in production and as SQLite-compatible
types under tests (``USE_SQLITE``), so the same ORM models can build the schema
from ``Base.metadata`` against an in-memory/temp SQLite database in the test
harness. Models import ``UUID`` / ``JSONB`` from here instead of
``sqlalchemy.dialects.postgresql``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CHAR
from sqlalchemy.dialects.postgresql import JSONB as _PGJSONB
from sqlalchemy.dialects.postgresql import UUID as _PGUUID
from sqlalchemy.types import JSON, TypeDecorator

__all__ = ["UUID", "JSONB"]


class UUID(TypeDecorator):
    """UUID type: PostgreSQL ``UUID`` natively, ``CHAR(32)`` hex on SQLite."""

    impl = CHAR
    cache_ok = True

    def __init__(self, as_uuid: bool = True, **kwargs) -> None:
        self.as_uuid = as_uuid
        super().__init__(**kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PGUUID(as_uuid=self.as_uuid))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        if self.as_uuid and not isinstance(value, uuid.UUID):
            return uuid.UUID(value)
        return value


class JSONB(TypeDecorator):
    """JSON type: PostgreSQL ``JSONB`` natively, generic ``JSON`` on SQLite."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(_PGJSONB())
        return dialect.type_descriptor(JSON())
