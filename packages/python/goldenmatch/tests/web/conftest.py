from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState

FIXTURES = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Copy sample project to a tmp dir so tests can write labels/yml safely."""
    import shutil
    dst = tmp_path / "project"
    shutil.copytree(FIXTURES, dst)
    return dst


@pytest.fixture
def client(sample_project: Path) -> TestClient:
    app = create_app(AppState.from_project_dir(sample_project))
    return TestClient(app)
