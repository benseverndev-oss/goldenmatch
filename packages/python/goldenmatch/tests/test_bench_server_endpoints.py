"""Endpoint-level traversal safety for /download and /logs (CodeQL #290-#293).

These serve the response path from a directory listing rather than a path built
from the request value, so the filesystem sink never sees user-derived input.
The tests pin the security behavior: valid name -> 200, traversal -> 400,
unknown name -> 404.
"""

from __future__ import annotations

from pathlib import Path

import bench_data_gen_server as srv
import pytest
from fastapi.testclient import TestClient

_TRAVERSAL = ["../x", "..\\x", "sub/x", "/etc/passwd", ".hidden", "", "a/../b"]


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setenv("GOLDENMATCH_BENCH_JOB_TOKEN", "tok")
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "LOGS_DIR", tmp_path / "logs")
    return TestClient(srv.app), {"Authorization": "Bearer tok"}


def test_download_serves_existing_file(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "bench_1000.parquet").write_bytes(b"DATA")
    client, h = _client(tmp_path, monkeypatch)
    r = client.get("/download", params={"file": "bench_1000.parquet"}, headers=h)
    assert r.status_code == 200
    assert r.content == b"DATA"


@pytest.mark.parametrize("bad", _TRAVERSAL)
def test_download_rejects_traversal(tmp_path: Path, monkeypatch, bad: str) -> None:
    client, h = _client(tmp_path, monkeypatch)
    r = client.get("/download", params={"file": bad}, headers=h)
    assert r.status_code == 400


def test_download_missing_is_404(tmp_path: Path, monkeypatch) -> None:
    client, h = _client(tmp_path, monkeypatch)
    r = client.get("/download", params={"file": "nope.parquet"}, headers=h)
    assert r.status_code == 404


def test_logs_serves_existing_log(tmp_path: Path, monkeypatch) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "job-1.log").write_text("hello")
    client, h = _client(tmp_path, monkeypatch)
    r = client.get("/logs", params={"job_id": "job-1"}, headers=h)
    assert r.status_code == 200
    assert "hello" in r.text


@pytest.mark.parametrize("bad", _TRAVERSAL)
def test_logs_rejects_traversal(tmp_path: Path, monkeypatch, bad: str) -> None:
    client, h = _client(tmp_path, monkeypatch)
    r = client.get("/logs", params={"job_id": bad}, headers=h)
    assert r.status_code == 400
