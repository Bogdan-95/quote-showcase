# app/cache.py
import json
import time
from collections import OrderedDict

from . import stats
from .config import CACHE_MAX_BYTES, CACHE_TTL_SECONDS


class QuoteCache:
    """
    LRU-кэш с TTL и лимитом по байтам.

    Хранит значения как raw JSON-байты (bytes), чтобы экономить память
    и отдавать их в HTTP-ответ без повторной сериализации.
    """

    def __init__(self, max_bytes: int = CACHE_MAX_BYTES, ttl: int = CACHE_TTL_SECONDS):
        self.cache: OrderedDict[str, tuple[bytes, float, int]] = OrderedDict()
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.current_bytes = 0

    def _get_size(self, item) -> int:
        if isinstance(item, bytes):
            return len(item)
        return len(json.dumps(item, ensure_ascii=False).encode("utf-8"))

    def get(self, key: str) -> bytes | None:
        if key not in self.cache:
            return None
        data, expiry, size = self.cache[key]
        if time.time() > expiry:
            self.invalidate(key)
            return None
        self.cache.move_to_end(key)
        return data

    def put(self, key: str, data):
        """Принимает dict или bytes. Внутри всегда хранит bytes."""
        if key in self.cache:
            self.invalidate(key)

        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")

        size = self._get_size(data)
        if size > self.max_bytes:
            return  # не влезает даже пустой кэш

        self.cache[key] = (data, time.time() + self.ttl, size)
        self.current_bytes += size
        self._evict_if_needed()
        self._sync_stats()

    def invalidate(self, key: str):
        """Удаление без инкремента evictions (ручной сброс)."""
        if key in self.cache:
            _, _, size = self.cache.pop(key)
            self.current_bytes -= size
            self._sync_stats()

    def _evict_if_needed(self):
        while self.current_bytes > self.max_bytes and self.cache:
            oldest_key = next(iter(self.cache))
            _, _, size = self.cache.pop(oldest_key)
            self.current_bytes -= size
            stats.STATS["evictions"] += 1
            self._sync_stats()

    def _sync_stats(self):
        stats.update_cache_stats(len(self.cache), self.current_bytes)

    def clear(self):
        self.cache.clear()
        self.current_bytes = 0
        self._sync_stats()


CACHE = QuoteCache()