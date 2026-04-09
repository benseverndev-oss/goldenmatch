"""Tests for benchmark/build_baseline.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
BENCHMARK_DIR = REPO_ROOT / "benchmark"
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

import build_baseline  # noqa: E402


def _minimal_report(language: str, f1: float = 0.5) -> dict:
    return {
        "version": 1,
        "language": language,
        "infermap_version": "0.1.0",
        "runner_version": "0.1.0",
        "ran_at": "2026-04-08T00:00:00Z",
        "duration_seconds": 1.0,
        "scorecard": {
            "overall": {"f1": f1, "top1": 0.5, "mrr": 0.5, "ece": 0.1, "n": 5},
            "by_difficulty": {},
            "by_category": {},
            "by_tag": {},
        },
        "per_case": [],
        "failed_cases": [],
    }


def test_writes_baseline_envelope(tmp_path):
    py_path = tmp_path / "py.json"
    ts_path = tmp_path / "ts.json"
    py_path.write_text(json.dumps(_minimal_report("python")), encoding="utf-8")
    ts_path.write_text(json.dumps(_minimal_report("typescript")), encoding="utf-8")

    out = tmp_path / "main.json"
    build_baseline.main_impl(
        python_path=py_path,
        ts_path=ts_path,
        commit="abc123",
        output=out,
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["commit"] == "abc123"
    assert data["python"]["language"] == "python"
    assert data["typescript"]["language"] == "typescript"
    assert "updated_at" in data


def test_writes_metadata_sidecar(tmp_path):
    py_path = tmp_path / "py.json"
    ts_path = tmp_path / "ts.json"
    py_path.write_text(json.dumps(_minimal_report("python")), encoding="utf-8")
    ts_path.write_text(json.dumps(_minimal_report("typescript")), encoding="utf-8")

    out = tmp_path / "main.json"
    build_baseline.main_impl(
        python_path=py_path,
        ts_path=ts_path,
        commit="abc123",
        output=out,
    )

    metadata_path = tmp_path / "main.metadata.json"
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["commit"] == "abc123"
    assert metadata["python_runner"] == "0.1.0"
    assert metadata["ts_runner"] == "0.1.0"
    assert "changelog" in metadata


def test_creates_parent_dir(tmp_path):
    py_path = tmp_path / "py.json"
    ts_path = tmp_path / "ts.json"
    py_path.write_text(json.dumps(_minimal_report("python")), encoding="utf-8")
    ts_path.write_text(json.dumps(_minimal_report("typescript")), encoding="utf-8")

    out = tmp_path / "new_dir" / "nested" / "main.json"
    build_baseline.main_impl(
        python_path=py_path,
        ts_path=ts_path,
        commit="x",
        output=out,
    )
    assert out.exists()
