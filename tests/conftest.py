import pytest

from app import catalog, stats
from app.cache import CACHE


@pytest.fixture(autouse=True)
def reset_global_state():
    catalog.CATALOG.clear()
    CACHE.clear()
    for key in stats.STATS:
        stats.STATS[key] = 0
    yield
