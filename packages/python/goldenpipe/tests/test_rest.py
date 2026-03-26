"""Tests for REST API."""
import pytest

try:
    from fastapi.testclient import TestClient
    from goldenpipe.api.server import create_app
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def client():
    return TestClient(create_app())


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestStagesEndpoint:
    def test_list_stages(self, client):
        r = client.get("/stages")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


class TestValidateEndpoint:
    def test_validate_empty(self, client):
        r = client.post("/validate", json={"pipeline": "test", "stages": []})
        assert r.status_code == 200


class TestRunEndpoint:
    def test_run_no_source(self, client):
        r = client.post("/run", json={"pipeline": "test", "stages": []})
        assert r.status_code == 200
