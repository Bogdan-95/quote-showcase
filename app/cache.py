# app/cache.py
import time
import json
from collections import OrderedDict
from typing import Optional, Dict, Any
from .config import CACHE_TTL_SECONDS, CACHE_MAX_BYTES
from . import stats


class QuoteCache:
    def __init__(self, max_bytes: int = CACHE_MAX_BYTES, ttl: int = CACHE_TTL_SECONDS):
        self.cache: OrderedDict = OrderedDict()
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.current_bytes = 0

    def _get_size(self, item: dict) -> int:
        return len(json.dumps(item).encode('utf-8'))

    def get(self, key: str) -> Optional[dict]:
        if key not in self.cache:
            return None
        data, expiry, size = self.cache[key]
        if time.time() > expiry:
            self.invalidate(key)
            return None
        self.cache.move_to_end(key)
        return data

    def put(self, key: str, data: dict):
        if key in self.cache:
            self.invalidate(key)

        size = self._get_size(data)
        if size > self.max_bytes:
            return

        self.cache[key] = (data, time.time() + self.ttl, size)
        self.current_bytes += size
        self._evict_if_needed()
        self._sync_stats()

    def invalidate(self, key: str):
        """Удаление без инкремента evictions (для ручного сброса)"""
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