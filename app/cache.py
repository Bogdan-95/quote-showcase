"""Byte-limited LRU cache with fixed TTL."""

import json
import time
from collections import OrderedDict
from typing import Any

from . import stats
from .config import CACHE_MAX_BYTES, CACHE_TTL_SECONDS


class QuoteCache:
    """LRU cache with insertion-based TTL and a maximum byte size."""

    def __init__(
        self,
        max_bytes: int = CACHE_MAX_BYTES,
        ttl: int = CACHE_TTL_SECONDS,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if ttl < 1:
            raise ValueError("ttl must be positive")

        self.cache: OrderedDict[str, tuple[bytes, float, int]] = OrderedDict()
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.current_bytes = 0

    @staticmethod
    def _serialize(data: Any) -> bytes:
        if isinstance(data, bytes):
            return data
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def get(self, key: str) -> bytes | None:
        entry = self.cache.get(key)
        if entry is None:
            return None

        data, expires_at, _size = entry
        if time.monotonic() >= expires_at:
            self.invalidate(key)
            return None

        self.cache.move_to_end(key)
        return data

    def put(self, key: str, data: dict[str, Any] | bytes) -> None:
        encoded = self._serialize(data)
        size = len(encoded)

        self.invalidate(key)
        if size > self.max_bytes:
            return

        self.cache[key] = (encoded, time.monotonic() + self.ttl, size)
        self.current_bytes += size
        self._evict_if_needed()
        self._sync_stats()

    def invalidate(self, key: str) -> None:
        entry = self.cache.pop(key, None)
        if entry is not None:
            self.current_bytes -= entry[2]
            self._sync_stats()

    def _evict_if_needed(self) -> None:
        while self.current_bytes > self.max_bytes and self.cache:
            oldest_key, (_, _, size) = self.cache.popitem(last=False)
            del oldest_key
            self.current_bytes -= size
            stats.STATS["evictions"] += 1

    def _sync_stats(self) -> None:
        stats.update_cache_stats(len(self.cache), self.current_bytes)

    def clear(self) -> None:
        self.cache.clear()
        self.current_bytes = 0
        self._sync_stats()


CACHE = QuoteCache()
