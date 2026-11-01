"""In-memory catalog and validated streaming snapshot import."""

import asyncio
import json
import tempfile
from typing import Any

from . import stats

# Raw JSON bytes avoid retaining a Python dict for every catalog record.
CATALOG: dict[str, bytes] = {}


def get_quote_from_catalog(quote_id: str) -> bytes | None:
    return CATALOG.get(quote_id)


def put_quote_to_catalog(quote_id: str, quote_data: dict[str, Any]) -> None:
    raw = json.dumps(quote_data, ensure_ascii=False).encode("utf-8")
    CATALOG[quote_id] = raw
    stats.update_catalog_stats(len(CATALOG))


def delete_quote_from_catalog(quote_id: str) -> bool:
    if quote_id not in CATALOG:
        return False

    del CATALOG[quote_id]
    stats.update_catalog_stats(len(CATALOG))
    return True


def _parse_record(raw_line: bytes, line_number: int) -> tuple[str, bytes]:
    line = raw_line.strip()
    if line.endswith(b","):
        line = line[:-1].rstrip()

    try:
        item = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in record on line {line_number}: {exc.msg}"
        ) from exc

    if not isinstance(item, dict):
        raise ValueError(f"Record on line {line_number} must be a JSON object")

    quote_id = item.get("id")
    author = item.get("author")
    quote_text = item.get("text")
    if not isinstance(quote_id, str) or not quote_id:
        raise ValueError(f"Record on line {line_number} has an invalid id")
    if not isinstance(author, str):
        raise ValueError(f"Record on line {line_number} has an invalid author")
    if not isinstance(quote_text, str):
        raise ValueError(f"Record on line {line_number} has an invalid text")

    return quote_id, line


async def import_catalog_stream(stream_reader) -> tuple[int, int]:
    """Validate a full snapshot before changing the in-memory catalog.

    The request body is spooled to disk after a small in-memory threshold.
    A first pass validates every record and collects IDs. A second pass
    updates the current dictionary in-place to avoid holding two full
    catalog dictionaries in memory at once.
    """
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as snapshot:
        async for chunk in stream_reader:
            snapshot.write(chunk)

        snapshot.seek(0)
        first_line = snapshot.readline().strip()
        if first_line != b'{"quotes":[':
            raise ValueError('Invalid format: expected {"quotes":[ as the first line')

        seen_ids: set[str] = set()
        imported = 0
        line_number = 1
        closed = False

        while True:
            raw_line = snapshot.readline()
            if not raw_line:
                break

            line_number += 1
            line = raw_line.strip()
            if not line:
                continue
            if line == b"]}":
                closed = True
                break

            quote_id, _ = _parse_record(line, line_number)
            if quote_id in seen_ids:
                raise ValueError(f"Duplicate quote id on line {line_number}: {quote_id}")
            seen_ids.add(quote_id)
            imported += 1

        if not closed:
            raise ValueError("Invalid format: missing closing ]} line")
        if snapshot.read().strip():
            raise ValueError("Invalid format: unexpected data after closing ]}")

        # Only a completely validated snapshot reaches this mutation phase.
        snapshot.seek(0)
        snapshot.readline()
        applied = 0
        while True:
            raw_line = snapshot.readline()
            if not raw_line:
                break

            line = raw_line.strip()
            if not line:
                continue
            if line == b"]}":
                break

            quote_id, raw_record = _parse_record(line, applied + 2)
            CATALOG[quote_id] = raw_record
            applied += 1

            if applied % 1000 == 0:
                await asyncio.sleep(0)

        stale_ids = [quote_id for quote_id in CATALOG if quote_id not in seen_ids]
        for quote_id in stale_ids:
            del CATALOG[quote_id]

    dropped = len(stale_ids)
    stats.update_catalog_stats(len(CATALOG))
    return imported, dropped
