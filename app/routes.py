"""HTTP routes for the Quote Showcase service."""

import asyncio
import weakref

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

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
LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def get_lock(quote_id: str) -> asyncio.Lock:
    lock = LOCKS.get(quote_id)
    if lock is None:
        lock = asyncio.Lock()
        LOCKS[quote_id] = lock
    return lock


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@router.get("/stats")
async def get_stats_endpoint() -> dict[str, int]:
    return stats.get_stats()


@router.get("/quotes/{quote_id}")
async def read_quote(quote_id: str) -> Response:
    cached = CACHE.get(quote_id)
    if cached is not None:
        stats.STATS["hits"] += 1
        return Response(
            content=cached,
            media_type="application/json",
            headers={"X-Cache": "HIT"},
        )

    # Misses count initial cache misses, including callers later coalesced
    # behind the same per-key lock. Loads count actual catalog reads.
    stats.STATS["misses"] += 1
    lock = get_lock(quote_id)

    async with lock:
        cached = CACHE.get(quote_id)
        if cached is not None:
            stats.STATS["hits"] += 1
            return Response(
                content=cached,
                media_type="application/json",
                headers={"X-Cache": "HIT"},
            )

        stats.STATS["loads"] += 1
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
async def import_snapshot(request: Request) -> dict[str, int] | JSONResponse:
    try:
        imported, dropped = await import_catalog_stream(request.stream())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    CACHE.clear()
    return {"imported": imported, "dropped": dropped}


@router.put("/catalog/{quote_id}")
async def update_catalog_item(quote_id: str, data: dict) -> dict[str, str]:
    author = data.get("author", "")
    quote_text = data.get("text", "")
    if (
        not isinstance(author, str)
        or not isinstance(quote_text, str)
        or not 1 <= len(author) <= MAX_AUTHOR_LEN
        or not 1 <= len(quote_text) <= MAX_TEXT_LEN
    ):
        raise HTTPException(status_code=422, detail="Invalid author or text length")

    quote = {"id": quote_id, "author": author, "text": quote_text}
    put_quote_to_catalog(quote_id, quote)
    CACHE.invalidate(quote_id)
    return quote


@router.delete("/catalog/{quote_id}")
async def delete_catalog_item(quote_id: str) -> dict[str, bool]:
    if not delete_quote_from_catalog(quote_id):
        raise HTTPException(status_code=404, detail="not found")

    CACHE.invalidate(quote_id)
    return {"deleted": True}
