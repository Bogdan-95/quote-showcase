import app.cache as cache_module
from app.cache import QuoteCache


def test_lru_evicts_least_recently_used_entry():
    cache = QuoteCache(max_bytes=4, ttl=60)
    cache.put("first", b"aa")
    cache.put("second", b"bb")

    assert cache.get("first") == b"aa"
    cache.put("third", b"cc")

    assert cache.get("first") == b"aa"
    assert cache.get("second") is None
    assert cache.get("third") == b"cc"
    assert cache.current_bytes == 4


def test_ttl_expires_from_insertion_time(monkeypatch):
    now = 100.0
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: now)
    cache = QuoteCache(max_bytes=20, ttl=5)
    cache.put("quote", b"hello")

    now = 104.9
    assert cache.get("quote") == b"hello"

    now = 105.0
    assert cache.get("quote") is None


def test_oversized_entry_is_not_cached():
    cache = QuoteCache(max_bytes=3, ttl=10)
    cache.put("large", b"four")

    assert cache.get("large") is None
    assert cache.current_bytes == 0
