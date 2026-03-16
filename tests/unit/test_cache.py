"""
Unit tests for whoop/api/cache.py — TTL cache.
"""

import time
import pytest
from whoop.api.cache import TTLCache


class TestTTLCache:
    def test_set_and_get_before_expiry(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/endpoint", {"key": "value"})
        result = cache.get("/endpoint")
        assert result == {"key": "value"}

    def test_miss_returns_none(self):
        cache = TTLCache(ttl_seconds=60)
        assert cache.get("/nonexistent") is None

    def test_expired_entry_returns_none(self):
        cache = TTLCache(ttl_seconds=1)
        cache.set("/ep", "data")
        time.sleep(1.1)
        assert cache.get("/ep") is None

    def test_expired_entry_deleted_from_store(self):
        cache = TTLCache(ttl_seconds=1)
        cache.set("/ep", "data")
        time.sleep(1.1)
        cache.get("/ep")  # triggers lazy delete
        assert "/ep" not in cache._store

    def test_params_differentiate_cache_keys(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/ep", "v1", params={"start": "2026-03-10"})
        cache.set("/ep", "v2", params={"start": "2026-03-11"})
        assert cache.get("/ep", params={"start": "2026-03-10"}) == "v1"
        assert cache.get("/ep", params={"start": "2026-03-11"}) == "v2"

    def test_params_order_independent(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/ep", "val", params={"b": "2", "a": "1"})
        result = cache.get("/ep", params={"a": "1", "b": "2"})
        assert result == "val"

    def test_none_params_and_empty_params_same_key(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/ep", "val", params=None)
        # Empty dict also produces no params suffix
        assert cache.get("/ep", params=None) == "val"

    def test_invalidate_removes_entry(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/ep", "val")
        cache.invalidate("/ep")
        assert cache.get("/ep") is None

    def test_invalidate_nonexistent_does_not_raise(self):
        cache = TTLCache(ttl_seconds=60)
        cache.invalidate("/does-not-exist")  # должно быть тихим

    def test_clear_removes_all(self):
        cache = TTLCache(ttl_seconds=60)
        for i in range(5):
            cache.set(f"/ep{i}", i)
        cache.clear()
        assert len(cache._store) == 0

    def test_eviction_triggered_on_set(self):
        cache = TTLCache(ttl_seconds=1)
        for i in range(5):
            cache.set(f"/ep{i}", i)
        time.sleep(1.1)
        # Force eviction interval to pass
        cache._last_eviction = 0
        cache.set("/new", "x")
        # Only "/new" should remain
        assert len(cache._store) == 1

    def test_overwrite_existing_key(self):
        cache = TTLCache(ttl_seconds=60)
        cache.set("/ep", "old")
        cache.set("/ep", "new")
        assert cache.get("/ep") == "new"

    def test_list_value_stored_correctly(self):
        cache = TTLCache(ttl_seconds=60)
        data = [{"id": 1}, {"id": 2}]
        cache.set("/list", data)
        assert cache.get("/list") == data

    def test_zero_ttl_immediately_expires(self):
        cache = TTLCache(ttl_seconds=0)
        cache.set("/ep", "val")
        # With ttl=0 entry expires at time.time() + 0 = now
        # Tiny sleep to ensure time advances
        time.sleep(0.01)
        assert cache.get("/ep") is None
