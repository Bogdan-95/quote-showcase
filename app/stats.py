# app/stats.py
import time

START_TIME = time.time()

STATS = {"hits": 0, "misses": 0, "loads": 0, "evictions": 0, "size": 0, "bytes": 0, "catalog": 0}


def get_stats():
    return STATS.copy()


def update_cache_stats(size: int, bytes_size: int):
    STATS["size"] = size
    STATS["bytes"] = bytes_size


def update_catalog_stats(count: int):
    STATS["catalog"] = count
