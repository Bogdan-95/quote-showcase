# app/routes.py
import asyncio
import weakref

from fastapi import APIRouter, HTTPException, Request, Response

from . import stats
from .cache import CACHE
from .catalog import (
    delete_quote_from_catalog,
    get_quote_from_catalog,
    import_catalog_stream,
    put_quote_to_catalog,
)
from .config import CATALOG_DELAY_SECONDS, MAX_AUTHOR_LEN, MAX_TEXT_LEN

router = APIRouter()

# WeakValueDictionary: лок удаляется GC, когда на него нет ссылок.
# Внутри async with лок удерживается локальной переменной.
LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def get_lock(quote_id: str) -> asyncio.Lock:
    lock = LOCKS.get(quote_id)
    if lock is None:
        lock = asyncio.Lock()
        LOCKS[quote_id] = lock
    return lock


@router.get("/health")
async def health():
    return {"status": "healthy"}


@router.get("/stats")
async def get_stats_endpoint():
    return stats.get_stats()


@router.get("/quotes/{quote_id}")
async def read_quote(quote_id: str):
    # 1. Быстрая проверка кэша
    cached = CACHE.get(quote_id)
    if cached is not None:
        stats.STATS["hits"] += 1
        return Response(
            content=cached,
            media_type="application/json",
            headers={"X-Cache": "HIT"},
        )

    stats.STATS["misses"] += 1
    lock = get_lock(quote_id)

    async with lock:
        # Двойная проверка: пока ждали лок, другой запрос мог заполнить кэш
        cached = CACHE.get(quote_id)
        if cached is not None:
            stats.STATS["hits"] += 1
            return Response(
                content=cached,
                media_type="application/json",
                headers={"X-Cache": "HIT"},
            )

        stats.STATS["loads"] += 1
        # Имитация дорогого чтения из каталога (>= 300 мс по контракту)
        await asyncio.sleep(CATALOG_DELAY_SECONDS)

        raw_bytes = get_quote_from_catalog(quote_id)
        if raw_bytes is None:
            raise HTTPException(status_code=404, detail="not found")

        CACHE.put(quote_id, raw_bytes)
        return Response(
            content=raw_bytes,
            media_type="application/json",
            headers={"X-Cache": "MISS"},
        )


@router.post("/import")
async def import_snapshot(request: Request):
    try:
        imported, dropped = await import_catalog_stream(request.stream())
        # Полная инвалидация кэша: снимок мог изменить старые записи
        CACHE.clear()
        return {"imported": imported, "dropped": dropped}
    except ValueError as e:
        return Response(
            content=f'{{"detail": "{e}"}}',
            status_code=400,
            media_type="application/json",
        )


@router.put("/catalog/{quote_id}")
async def update_catalog_item(quote_id: str, data: dict):
    author = data.get("author", "")
    text = data.get("text", "")
    if not (1 <= len(author) <= MAX_AUTHOR_LEN) or not (1 <= len(text) <= MAX_TEXT_LEN):
        raise HTTPException(status_code=422, detail="Invalid author or text length")

    quote = {"id": quote_id, "author": author, "text": text}
    put_quote_to_catalog(quote_id, quote)
    CACHE.invalidate(quote_id)
    return quote


@router.delete("/catalog/{quote_id}")
async def delete_catalog_item(quote_id: str):
    if not delete_quote_from_catalog(quote_id):
        raise HTTPException(status_code=404, detail="not found")

    CACHE.invalidate(quote_id)
    return {"deleted": True}