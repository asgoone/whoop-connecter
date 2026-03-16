"""Simple in-memory TTL cache, keyed by (endpoint, params).

Expired entries are evicted lazily on get() and on every set() call.
"""

import time
from dataclasses import dataclass
from typing import Any

_EVICTION_INTERVAL = 60  # seconds between full-sweep evictions


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _Entry] = {}
        self._last_eviction: float = time.time()

    def _make_key(self, endpoint: str, params: dict | None) -> str:
        if not params:
            return endpoint
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{endpoint}?{sorted_params}"

    def _evict_expired(self) -> None:
        now = time.time()
        if now - self._last_eviction < _EVICTION_INTERVAL:
            return
        expired_keys = [k for k, e in self._store.items() if now > e.expires_at]
        for k in expired_keys:
            del self._store[k]
        self._last_eviction = now

    def get(self, endpoint: str, params: dict | None = None) -> Any | None:
        key = self._make_key(endpoint, params)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, endpoint: str, value: Any, params: dict | None = None) -> None:
        self._evict_expired()
        key = self._make_key(endpoint, params)
        self._store[key] = _Entry(value=value, expires_at=time.time() + self._ttl)

    def invalidate(self, endpoint: str, params: dict | None = None) -> None:
        key = self._make_key(endpoint, params)
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
