from fastapi.testclient import TestClient

from app.catalog import CATALOG
from main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_quote_response_reports_cache_status():
    CATALOG["q-1"] = b'{"id":"q-1","author":"Author","text":"A quote"}'

    first = client.get("/quotes/q-1")
    second = client.get("/quotes/q-1")

    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert second.status_code == 200
    assert second.headers["x-cache"] == "HIT"
    assert first.json()["id"] == "q-1"


def test_invalid_import_returns_json_error_without_partial_mutation():
    CATALOG["existing"] = b'{"id":"existing","author":"A","text":"old"}'

    response = client.post(
        "/import",
        content=b'{"quotes":[\n{"id":"new","author":"A","text":}\n]}\n',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid JSON")
    assert set(CATALOG) == {"existing"}
