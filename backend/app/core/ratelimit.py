"""In-process rate limiting for auth-adjacent endpoints (ADR 0018 §1).

Counts FAILURES within a sliding window, keyed by (client IP, subject). Only
failed attempts are throttled — successful logins are never limited, and a
success clears the failure history for its key. That blocks credential
brute-forcing without ever locking out a household that logs in a lot.

In-process on purpose: a household instance is a single backend process, so a
shared store would be overkill. The activation endpoint (ADR 0018 §2) reuses
this when it lands.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class FailureRateLimiter:
    def __init__(self, *, max_failures: int = 5, window_seconds: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> None:
        window = self._failures[key]
        while window and now - window[0] > self.window_seconds:
            window.popleft()
        if not window:
            self._failures.pop(key, None)

    def check(self, key: str) -> None:
        """Raise 429 when the key has exhausted its failure budget."""
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            if len(self._failures.get(key, ())) >= self.max_failures:
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed attempts; try again in a few minutes.",
                )

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.monotonic())

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# Shared limiter for login (and, later, activation) attempts.
login_limiter = FailureRateLimiter()
