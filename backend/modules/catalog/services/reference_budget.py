"""Cooperative request-wide budget for optional inline reference enrichment."""
import time
from contextlib import contextmanager
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("reference_deadline", default=None)


def remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def expired() -> bool:
    value = remaining()
    return value is not None and value <= 0


def transport_timeout(default: float) -> float:
    value = remaining()
    if value is not None and value <= 0:
        raise TimeoutError("Inline reference budget exhausted")
    # HTTPX has four independently timed transport phases. Reserve their share
    # rather than giving every phase the entire remaining request budget.
    return default if value is None else min(default, value / 4)


@contextmanager
def budget(seconds: float):
    parent = _deadline.get()
    deadline = time.monotonic() + seconds
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)
