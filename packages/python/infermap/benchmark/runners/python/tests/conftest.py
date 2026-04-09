"""Shared pytest fixtures for infermap_bench."""
from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory holding hand-authored fixture cases."""
    return HERE / "fixture"
