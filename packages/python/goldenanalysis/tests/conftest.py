"""Shared test fixtures for goldenanalysis.

Fixture files are anchored to ``__file__`` (never a bare relative path) so tests
pass whether CWD is the package dir (local) or the repo root (CI). See the repo
CLAUDE.md "Test fixture paths" note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
