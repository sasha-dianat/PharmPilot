"""Global harvest mutex — NFI crawl and دارونامه harvests share the Iran system
proxy, so only ONE may run at a time. Owner-tagged so GUIs can say who holds it."""
from __future__ import annotations

import threading

_guard = threading.Lock()
_holder: str | None = None


def acquire(owner: str) -> bool:
    global _holder
    with _guard:
        if _holder is not None:
            return False
        _holder = owner
        return True


def release(owner: str) -> None:
    global _holder
    with _guard:
        if _holder == owner:
            _holder = None


def holder() -> str | None:
    return _holder


def force_release() -> None:
    """Test/emergency use only."""
    global _holder
    with _guard:
        _holder = None
