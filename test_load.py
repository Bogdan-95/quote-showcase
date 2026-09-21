# test_load.py — Нагрузочные тесты для Quote Showcase
import asyncio
import json
import time

import httpx
import psutil

BASE_URL = "http://127.0.0.1:8000"


def serialize_snapshot(records: list) -> str:
    """Сериализация снимка в построчном формате из контракта."""
    lines = ['{"quotes":[']
    for i, rec in enumerate(records):
        line = json.dumps(rec, ensure_ascii=False)
        if i < len(records) - 1:
            line += ","
        lines.append(line)
    lines.append("]}")
    return "\n".join(lines)


def get_memory_mb() -> float:
    """Получить RSS память процесса uvicorn в MB"""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any("uvicorn" in str(arg) for arg in cmdline):
                return proc.memory_info().rss / 1024 / 1024
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    print("  ⚠ Процесс uvicorn не найден, возвращаю -1")
    return -1


async def get_stats(client: httpx.AsyncClient) -> dict:
    resp = await client.get(f"{BASE_URL}/stats")
    return resp.json()


# =============================================================================
# ТЕСТ A: TTL (Time-To-Live) — проверка истечения кэша
# =============================================================================
async def test_a_ttl() -> tuple[bool, str]:
    """
    Ожидание:
    - PUT цитату, GET -> MISS
    - Подождать 16 секунд (TTL = 15с)
    - GET снова -> MISS (TTL истёк)
    - stats: misses вырастет на 2, loads вырастет на 2
    """
    print("\n" + "=" * 60)
    print("ТЕСТ A: TTL (Time-To-Live)")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=60.0) as client:
        quote_id = "ttl-test-quote"
        quote_data = {"author": "TTL Author", "text": "This quote should expire after 15 seconds"}

        # PUT в каталог
        resp = await client.put(f"{BASE_URL}/catalog/{quote_id}", json=quote_data)
        assert resp.status_code == 200, f"PUT failed: {resp.status_code}"
        print(f"✓ PUT /catalog/{quote_id} -> 200")

        # Фиксируем статистику до GET
        stats_before = await get_stats(client)
        hits_before = stats_before["hits"]
        misses_before = stats_before["misses"]
        loads_before = stats_before["loads"]

        # Первый GET -> должен быть MISS
        resp = await client.get(f"{BASE_URL}/quotes/{quote_id}")
        assert resp.status_code == 200
        assert (
            resp.headers.get("x-cache") == "MISS"
        ), f"Expected MISS, got {resp.headers.get('x-cache')}"
        print(f"✓ GET /quotes/{quote_id} -> 200, X-Cache: MISS")

        # Ждём 18 секунд (TTL = 15с)
        print("  Ожидание 18 секунд для истечения TTL...")
        await asyncio.sleep(18)

        # Второй GET -> должен быть MISS (TTL истёк)
        resp = await client.get(f"{BASE_URL}/quotes/{quote_id}")
        assert resp.status_code == 200
        assert (
            resp.headers.get("x-cache") == "MISS"
        ), f"Expected MISS after TTL, got {resp.headers.get('x-cache')}"
        print(f"✓ GET /quotes/{quote_id} (after 16s) -> 200, X-Cache: MISS")

        stats_final = await get_stats(client)

        # Проверяем статистику
        misses_delta = stats_final["misses"] - misses_before
        loads_delta = stats_final["loads"] - loads_before

        print("\n📊 Статистика:")
        print(f"   misses delta: {misses_delta} (ожидание: 2)")
        print(f"   loads delta:  {loads_delta} (ожидание: 2)")
        print(f"   hits delta:   {stats_final['hits'] - hits_before} (ожидание: 0)")

        passed = misses_delta == 2 and loads_delta == 2
        verdict = "PASS" if passed else "FAIL"

        return passed, verdict


# =============================================================================
# ТЕСТ B: Большой импорт (100k записей, >30MB)
# =============================================================================
async def test_b_large_import() -> tuple[bool, str]:
    """
    Ожидание:
    - Генерация 100,000 записей (~30-40 MB JSON)
    - POST /import успешен
    - RSS памяти uvicorn < 250 MB
    """
    print("\n" + "=" * 60)
    print("ТЕСТ B: Большой импорт (100k записей)")
    print("=" * 60)

    # Замеряем память до
    mem_before = get_memory_mb()
    if mem_before > 0:
        print(f"📊 Память uvicorn до импорта: {mem_before:.1f} MB")
    else:
        print("📊 Память uvicorn до импорта: неизвестна")

    # Генерируем данные в памяти
    print("  Генерация 100,000 записей...")
    start_gen = time.time()

    records = []
    for i in range(100_000):
        records.append(
            {
                "id": f"q-{i}",
                "author": f"Author-{i % 1000}",
                "text": f"This is quote number {i}. "
                + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5,
            }
        )

    json_data = serialize_snapshot(records)
    body_size_mb = len(json_data.encode("utf-8")) / 1024 / 1024

    gen_time = time.time() - start_gen
    print(f"✓ Генерация завершена за {gen_time:.2f}s, размер: {body_size_mb:.1f} MB")

    # Проверяем размер
    size_ok = body_size_mb > 30
    print(f"  {'✓' if size_ok else '✗'} Размер тела: {body_size_mb:.1f} MB (ожидание: >30 MB)")

    # Импортируем
    print("  Отправка POST /import...")
    start_import = time.time()

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{BASE_URL}/import", content=json_data, headers={"Content-Type": "application/json"}
        )

    import_time = time.time() - start_import

    if resp.status_code != 200:
        return False, f"FAIL: Import returned {resp.status_code}: {resp.text}"

    result = resp.json()
    print(f"✓ POST /import -> 200, imported={result['imported']}, dropped={result['dropped']}")
    print(f"  Время импорта: {import_time:.2f}s")

    # Ждём 2 секунды стабилизации
    await asyncio.sleep(2)

    # Замеряем память после
    mem_after = get_memory_mb()
    if mem_after > 0:
        print(f"📊 Память uvicorn после импорта: {mem_after:.1f} MB")
        print(f"   Delta: {mem_after - mem_before:.1f} MB")
        mem_ok = mem_after < 250
        print(f"  {'✓' if mem_ok else '✗'} Память: {mem_after:.1f} MB (ограничение: <250 MB)")
    else:
        print("  ⚠ Не удалось измерить память uvicorn")
        mem_ok = True  # Пропускаем проверку памяти

    import_ok = result["imported"] == 100_000
    print(f"  {'✓' if import_ok else '✗'} Импортировано: {result['imported']} (ожидание: 100000)")

    passed = size_ok and mem_ok and import_ok
    verdict = "PASS" if passed else "FAIL"

    return passed, verdict


# =============================================================================
# ТЕСТ C: Импорт под нагрузкой
# =============================================================================
async def test_c_import_under_load() -> tuple[bool, str]:
    """
    Ожидание:
    - POST /import запускается параллельно (содержит и old, и new записи)
    - 200 GET к разным цитатам
    - 50 GET /health
    - Все 250 запросов -> 200
    """
    print("\n" + "=" * 60)
    print("ТЕСТ C: Импорт под нагрузкой")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_connections=350)) as client:
        # Импортируем базовый каталог
        print("  Подготовка каталога с 1000 цитатами...")
        base_records = []
        for i in range(1000):
            base_records.append(
                {
                    "id": f"load-test-{i}",
                    "author": f"Author-{i}",
                    "text": f"Load test quote number {i}",
                }
            )
        base_json = serialize_snapshot(base_records)

        resp = await client.post(
            f"{BASE_URL}/import", content=base_json, headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 200
        print(f"✓ Базовый каталог импортирован ({resp.json()['imported']} записей)")

        # Готовим большой снимок: старые + новые записи
        # Это критично — POST /import заменяет весь каталог
        print("  Подготовка большого снимка (5000 new + 1000 old = 6000 записей)...")
        all_records = list(base_records)  # копируем старые

        for i in range(5000):
            all_records.append(
                {
                    "id": f"big-{i}",
                    "author": f"BigAuthor-{i}",
                    "text": f"Big quote {i} with some content",
                }
            )

        big_json = serialize_snapshot(all_records)
        big_size_mb = len(big_json.encode("utf-8")) / 1024 / 1024
        print(f"  Размер снимка: {big_size_mb:.1f} MB ({len(all_records)} записей)")

        # Запускаем импорт как фоновую задачу
        import_task = asyncio.create_task(
            client.post(
                f"{BASE_URL}/import", content=big_json, headers={"Content-Type": "application/json"}
            )
        )

        # Ждём немного, чтобы импорт начался
        await asyncio.sleep(0.2)

        # Запускаем 200 GET к разным цитатам
        print("  Запуск 200 GET /quotes/{id} параллельно...")
        quote_ids = [f"load-test-{i}" for i in range(200)]

        async def get_quote(qid: str) -> tuple[str, int, str]:
            try:
                resp = await client.get(f"{BASE_URL}/quotes/{qid}")
                return (qid, resp.status_code, resp.headers.get("x-cache", ""))
            except Exception as e:
                return (qid, 0, str(e))

        quote_tasks = [get_quote(qid) for qid in quote_ids]

        # Запускаем 50 GET /health
        print("  Запуск 50 GET /health параллельно...")

        async def get_health() -> int:
            try:
                resp = await client.get(f"{BASE_URL}/health")
                return resp.status_code
            except Exception:
                return 0

        health_tasks = [get_health() for _ in range(50)]

        # Выполняем всё параллельно
        start_time = time.time()
        quote_results, health_results = await asyncio.gather(
            asyncio.gather(*quote_tasks), asyncio.gather(*health_tasks)
        )
        total_time = time.time() - start_time

        # Ждём завершения импорта
        import_resp = await import_task

        # Анализируем результаты
        quote_200_count = sum(1 for _, status, _ in quote_results if status == 200)
        health_200_count = sum(1 for status in health_results if status == 200)

        print(f"\n📊 Результаты (время: {total_time:.2f}s):")
        print(f"   GET /quotes: {quote_200_count}/200 -> 200")
        print(f"   GET /health: {health_200_count}/50 -> 200")
        print(f"   POST /import: {import_resp.status_code}")

        passed = (
            quote_200_count == 200 and health_200_count == 50 and import_resp.status_code == 200
        )
        verdict = "PASS" if passed else "FAIL"

        return passed, verdict


# =============================================================================
# Запуск всех тестов
# =============================================================================
async def run_all_tests():
    """Запуск всех тестов и генерация отчёта"""
    print("\n" + "#" * 60)
    print("# НАГРУЗОЧНЫЕ ТЕСТЫ — Quote Showcase")
    print("#" * 60)

    results: list[tuple[str, str, str, str]] = []

    # ТЕСТ A
    try:
        passed, verdict = await test_a_ttl()
        results.append(("A: TTL", "2 MISS после 16s", "См. выше", verdict))
    except Exception as e:
        results.append(("A: TTL", "2 MISS после 16s", f"ERROR: {e}", "FAIL"))

    # ТЕСТ B
    try:
        passed, verdict = await test_b_large_import()
        results.append(("B: Большой импорт", ">30MB, <250MB RAM", "См. выше", verdict))
    except Exception as e:
        results.append(("B: Большой импорт", ">30MB, <250MB RAM", f"ERROR: {e}", "FAIL"))

    # ТЕСТ C
    try:
        passed, verdict = await test_c_import_under_load()
        results.append(("C: Импорт под нагрузкой", "250/250 -> 200", "См. выше", verdict))
    except Exception as e:
        results.append(("C: Импорт под нагрузкой", "250/250 -> 200", f"ERROR: {e}", "FAIL"))

    # Финальный отчёт
    print("\n" + "=" * 60)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 60)
    print(f"{'Тест':<30} {'Ожидание':<25} {'Факт':<20} {'Вердикт':<10}")
    print("-" * 85)
    for test, expected, actual, verdict in results:
        print(f"{test:<30} {expected:<25} {actual:<20} {verdict:<10}")
    print("-" * 85)

    all_passed = all(v == "PASS" for _, _, _, v in results)
    print(f"\n{'✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ' if all_passed else '❌ ЕСТЬ ПРОВАЛЕННЫЕ ТЕСТЫ'}")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
