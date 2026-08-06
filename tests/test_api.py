from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_add_endpoint():
    r = client.get("/add", params={"a": 2, "b": 3})
    assert r.status_code == 200
    assert r.json() == {"result": 5}


def test_multiply_endpoint():
    r = client.get("/multiply", params={"a": 4, "b": 3})
    assert r.status_code == 200
    assert r.json() == {"result": 12}


def test_divide_endpoint():
    r = client.get("/divide", params={"a": 10, "b": 2})
    assert r.status_code == 200
    assert r.json() == {"result": 5}


def test_divide_by_zero_endpoint():
    r = client.get("/divide", params={"a": 1, "b": 0})
    assert r.status_code == 400
