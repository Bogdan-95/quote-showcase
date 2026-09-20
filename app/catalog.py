# app/catalog.py
import json
from typing import Dict, Any, Optional
from . import stats

CATALOG: Dict[str, dict] = {}


def get_quote_from_catalog(quote_id: str) -> Optional[dict]:
    return CATALOG.get(quote_id)


def put_quote_to_catalog(quote_id: str, quote_data: dict):
    CATALOG[quote_id] = quote_data
    stats.update_catalog_stats(len(CATALOG))


def delete_quote_from_catalog(quote_id: str) -> bool:
    if quote_id in CATALOG:
        del CATALOG[quote_id]
        stats.update_catalog_stats(len(CATALOG))
        return True
    return False


async def _iter_lines(byte_stream):
    """Разбивает async-поток байтов на строки, буферизуя остатки."""
    buffer = b''
    async for chunk in byte_stream:
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            yield line
    if buffer:
        yield buffer


async def import_catalog_stream(stream_reader) -> tuple:
    """
    Стриминговый парсинг JSON-снимка.

    Формат гарантирован заданием:
    - Первая строка: {"quotes":[
    - Далее по одной записи в строке, записи разделены запятой в конце строки
    - Последняя строка: ]}

    Парсим построчно — в памяти всегда только одна цитата (~16 КБ max).
    """
    new_catalog: Dict[str, dict] = {}
    imported = 0
    line_num = 0

    async for raw_line in _iter_lines(stream_reader):
        line_num += 1
        line = raw_line.strip()
        if not line:
            continue

        # Первая строка — префикс {"quotes":[
        if line_num == 1:
            if not line.startswith(b'{'):
                raise ValueError("Invalid format: expected JSON object start")
            continue

        # Последняя строка — ]}
        if line == b']}':
            break

        # Убираем trailing comma
        if line.endswith(b','):
            line = line[:-1]

        # Парсим одну запись
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in record #{line_num}: {str(e)}")

        if not isinstance(item, dict):
            raise ValueError(f"Record #{line_num} is not an object")
        if 'id' not in item or 'author' not in item or 'text' not in item:
            raise ValueError(f"Record #{line_num} missing required fields")

        new_catalog[item['id']] = item
        imported += 1

    old_ids = set(CATALOG.keys())
    new_ids = set(new_catalog.keys())
    dropped = len(old_ids - new_ids)

    CATALOG.clear()
    CATALOG.update(new_catalog)
    stats.update_catalog_stats(len(CATALOG))

    return imported, dropped