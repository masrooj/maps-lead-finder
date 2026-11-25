"""Process-local TTL cache for hot shared GETs (web + mobile).

Prisma Postgres bills per SQL statement — cache hits avoid the DB entirely.
Not shared across Vercel isolates; still wins on warm instances.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)


def get_or_set(key: str, ttl_s: float, loader: Callable[[], Any]) -> Any:
    """Return cached value if fresh; otherwise call loader, store, return."""
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    value = loader()
    expires = time.monotonic() + max(0.0, float(ttl_s))
    with _lock:
        _store[key] = (expires, value)
    return value


def invalidate(prefix_or_key: str) -> int:
    """Drop exact key or all keys starting with prefix. Returns number removed."""
    needle = prefix_or_key or ""
    removed = 0
    with _lock:
        if needle.endswith(":") or needle.endswith("*"):
            prefix = needle.rstrip("*")
        else:
            # Exact key + children with "key:" prefix
            if needle in _store:
                del _store[needle]
                removed += 1
            prefix = needle + ":" if needle else ""
        if prefix:
            for k in list(_store.keys()):
                if k.startswith(prefix):
                    del _store[k]
                    removed += 1
    return removed


def clear_all() -> None:
    with _lock:
        _store.clear()


def invalidate_queue_caches() -> int:
    """Clear notification queues, CQ meta, and shared CQ universe caches."""
    n = 0
    for prefix in ("queues:", "cqmeta:", "cquniverse:", "stats:", "appsetting:"):
        n += invalidate(prefix)
    return n


# TTLs from Prisma ops plan
TTL_QUEUES_S = 45.0
TTL_CQ_META_S = 30.0
TTL_STATS_S = 30.0
TTL_CQ_UNIVERSE_S = 30.0
TTL_APP_SETTING_S = 90.0
