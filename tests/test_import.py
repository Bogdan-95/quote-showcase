import asyncio
import json

import pytest

from app import catalog


async def byte_stream(payload: bytes, chunk_size: int = 7):
    for offset in range(0, len(payload), chunk_size):
        yield payload[offset : offset + chunk_size]


def make_snapshot(records: list[dict]) -> bytes:
    rows = [json.dumps(record, ensure_ascii=False).encode("utf-8") for record in records]
    body = b'{"quotes":[\n'
    body += b",\n".join(rows)
    return body + b"\n]}\n"


def test_valid_snapshot_replaces_catalog_and_reports_dropped(monkeypatch):
    catalog.CATALOG.clear()
    catalog.CATALOG["keep"] = b'{"id":"keep","author":"old","text":"old"}'
    catalog.CATALOG["remove"] = b'{"id":"remove","author":"x","text":"x"}'

    payload = make_snapshot(
        [
            {"id": "keep", "author": "new", "text": "updated"},
            {"id": "add", "author": "author", "text": "new quote"},
        ]
    )
    imported, dropped = asyncio.run(catalog.import_catalog_stream(byte_stream(payload)))

    assert imported == 2
    assert dropped == 1
    assert set(catalog.CATALOG) == {"keep", "add"}
    assert json.loads(catalog.CATALOG["keep"])["text"] == "updated"


def test_invalid_snapshot_does_not_mutate_existing_catalog():
    original = {"existing": b'{"id":"existing","author":"A","text":"old"}'}
    catalog.CATALOG.clear()
    catalog.CATALOG.update(original)

    invalid = b'{"quotes":[\n{"id":"new","author":"A","text":}\n]}\n'
    with pytest.raises(ValueError, match="Invalid JSON"):
        asyncio.run(catalog.import_catalog_stream(byte_stream(invalid)))

    assert catalog.CATALOG == original


def test_duplicate_ids_are_rejected_before_catalog_mutation():
    original = {"existing": b'{"id":"existing","author":"A","text":"old"}'}
    catalog.CATALOG.clear()
    catalog.CATALOG.update(original)

    payload = make_snapshot(
        [
            {"id": "duplicate", "author": "A", "text": "one"},
            {"id": "duplicate", "author": "A", "text": "two"},
        ]
    )
    with pytest.raises(ValueError, match="Duplicate quote id"):
        asyncio.run(catalog.import_catalog_stream(byte_stream(payload)))

    assert catalog.CATALOG == original
