# app/routes.py
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import asyncio
import weakref
from .config import CATALOG_DELAY_SECONDS, MAX_AUTHOR_LEN, MAX_TEXT_LEN
from .cache import CACHE
from .catalog import (
    get_quote_from_catalog,
    put_quote_to_catalog,
    delete_quote_from_catalog,
    import_catalog_stream,
)
from . import stats

router = APIRouter()


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
    cached = CACHE.get(quote_id)
    if cached:
        stats.STATS["hits"] += 1
        return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

    stats.STATS["misses"] += 1
    lock = get_lock(quote_id)

    async with lock:
        cached = CACHE.get(quote_id)
        if cached:
            stats.STATS["hits"] += 1
            return JSONResponse(content=cached, headers={"X-Cache": "HIT"})

        stats.STATS["loads"] += 1
        await asyncio.sleep(CATALOG_DELAY_SECONDS)

        quote = get_quote_from_catalog(quote_id)
        if not quote:
            raise HTTPException(status_code=404, detail="not found")

        CACHE.put(quote_id, quote)
        return JSONResponse(content=quote, headers={"X-Cache": "MISS"})


@router.post("/import")
async def import_snapshot(request: Request):
    try:
        imported, dropped = await import_catalog_stream(request.stream())
        CACHE.clear()
        return {"imported": imported, "dropped": dropped}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})


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