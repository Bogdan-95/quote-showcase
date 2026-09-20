# app/catalog.py
import json

from . import stats

# Храним raw JSON-байты записи, а не dict — экономит ~50% памяти.
CATALOG: dict[str, bytes] = {}


def get_quote_from_catalog(quote_id: str) -> bytes | None:
    """Возвращает raw JSON-байты записи или None."""
    return CATALOG.get(quote_id)


def put_quote_to_catalog(quote_id: str, quote_data: dict):
    """PUT /catalog/{id} — сериализуем dict в JSON-байты для хранения."""
    raw = json.dumps(quote_data, ensure_ascii=False).encode("utf-8")
    CATALOG[quote_id] = raw
    stats.update_catalog_stats(len(CATALOG))


def delete_quote_from_catalog(quote_id: str) -> bool:
    if quote_id in CATALOG:
        del CATALOG[quote_id]
        stats.update_catalog_stats(len(CATALOG))
        return True
    return False


async def _iter_lines(byte_stream):
    """Разбивает async-поток байтов на строки, буферизуя остатки."""
    buffer = b""
    async for chunk in byte_stream:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
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
    Храним raw JSON-байты, чтобы не тратить память на dict.

    Каталог обновляется in-place: новые записи перезаписываются сразу,
    после чего удаляются те, что не пришли в снимке. Так избегаем
    одновременного хранения старого и нового каталога (пик памяти ×2).
    """
    imported = 0
    line_num = 0
    seen_ids: set[str] = set()

    async for raw_line in _iter_lines(stream_reader):
        line_num += 1
        line = raw_line.strip()
        if not line:
            continue

        # Первая строка — префикс {"quotes":[
        if line_num == 1:
            if not line.startswith(b"{"):
                raise ValueError("Invalid format: expected JSON object start")
            continue

        # Последняя строка — ]}
        if line == b"]}":
            break

        # Убираем trailing comma
        if line.endswith(b","):
            line = line[:-1]

        # Парсим одну запись — только для валидации, в каталог пойдут сырые байты
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in record #{line_num}: {str(e)}")

        if not isinstance(item, dict):
            raise ValueError(f"Record #{line_num} is not an object")
        if "id" not in item or "author" not in item or "text" not in item:
            raise ValueError(f"Record #{line_num} missing required fields")

        quote_id = item["id"]
        CATALOG[quote_id] = line  # in-place, raw bytes
        seen_ids.add(quote_id)
        imported += 1

    # Удаляем записи, которых не было в новом снимке
    dropped = 0
    for old_id in list(CATALOG.keys()):
        if old_id not in seen_ids:
            del CATALOG[old_id]
            dropped += 1

    stats.update_catalog_stats(len(CATALOG))
    return imported, dropped
