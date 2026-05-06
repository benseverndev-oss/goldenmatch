"""Compose real domain packs + test fixtures into a single dir for tests."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _domain_dir(monkeypatch, tmp_path):
    real = (
        Path(__file__).resolve().parents[3]
        / "typescript"
        / "goldencheck-types"
        / "domains"
    )
    fixtures = Path(__file__).resolve().parent / "fixtures"
    composite = tmp_path / "domains"
    composite.mkdir()
    for src in [real, fixtures]:
        if src.exists():
            for f in src.glob("*.yaml"):
                (composite / f.name).write_bytes(f.read_bytes())
    monkeypatch.setenv("GOLDENCHECK_TYPES_TEST_DIR", str(composite))
