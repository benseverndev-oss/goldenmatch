import json
from pathlib import Path

from fastapi.testclient import TestClient

from goldenflow.api.server import create_app


def test_health():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_transforms():
    client = TestClient(create_app())
    response = client.get("/transforms")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_transform_endpoint(sample_csv: Path):
    client = TestClient(create_app())
    with open(sample_csv, "rb") as f:
        response = client.post("/transform", files={"file": ("data.csv", f, "text/csv")})
    assert response.status_code == 200
    data = response.json()
    assert "manifest" in data
    assert "data" in data
