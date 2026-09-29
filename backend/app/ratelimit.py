"""In-memory attempt limiter: 5 failures within 15 minutes blocks that key (login, sign-up code, patient link)."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException

WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5
_failures: dict[str, deque] = defaultdict(deque)


def check(key: str) -> None:
    q = _failures[key]
    while q and q[0] < time.time() - WINDOW_SECONDS:
        q.popleft()
    if len(q) >= MAX_FAILURES:
        raise HTTPException(429, "Too many attempts. Please wait 15 minutes and try again.")


def fail(key: str) -> None:
    _failures[key].append(time.time())


def clear(key: str) -> None:
    _failures.pop(key, None)


def reset_all() -> None:
    _failures.clear()
