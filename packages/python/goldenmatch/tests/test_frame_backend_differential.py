"""Frame-backend differential gate (Polars-eviction W1, Task 6).

Two frozen fixture datasets (``tests/fixtures/frame_diff/*.json``) pin the
canonical, order-stable output of the file-based ``run_dedupe`` pipeline
under an explicit deterministic config (exact + weighted matchkeys, static
blocking, no EM/rerank/auto-config -- see
``scripts/diff_frame_backends.py`` for the full rationale + config).

- Test A is a regression anchor: today's ``GOLDENMATCH_FRAME=polars`` output
  must still match the frozen JSON. A failure here means the PIPELINE
  changed (clustering/scoring/golden survivorship), not the frame backend --
  re-freeze deliberately (``python scripts/diff_frame_backends.py --freeze
  tests/fixtures/frame_diff``) after confirming the new output is correct,
  don't patch this test to match.
- Test B is the actual differential gate for the Polars-eviction program:
  ``GOLDENMATCH_FRAME=arrow`` must reproduce the SAME frozen JSON. A failure
  here means the arrow ingest path (or anything wired to
  ``GOLDENMATCH_FRAME``) diverged from the polars reference.

Both tests import ``scripts/diff_frame_backends.py`` via ``importlib`` (path
anchored to this file, not CWD -- CWD differs between local runs and CI) so
there is exactly ONE canonicalization implementation, not a duplicate copy
drifting out of sync with the standalone runner.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

# tests/ -> goldenmatch/ -> python/ -> packages/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "diff_frame_backends.py"
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "frame_diff"


def _load_script_module() -> ModuleType:
    if not _SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"diff_frame_backends.py not found at {_SCRIPT_PATH} -- repo "
            "layout assumption (tests/ 4 levels under repo root) broke."
        )
    spec = importlib.util.spec_from_file_location(
        "goldenmatch_diff_frame_backends", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_script_module()
_DATASET_NAMES = sorted(_mod.DATASETS)


def _load_frozen(name: str) -> dict:
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Frozen fixture missing: {path}. Generate via "
            f"'python scripts/diff_frame_backends.py --freeze "
            f"{_FIXTURES_DIR}'."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_fixtures_present_for_every_dataset() -> None:
    """Sanity check the fixture set isn't stale relative to the script's
    dataset registry (e.g. a dataset added to the script but never frozen)."""
    for name in _DATASET_NAMES:
        assert (_FIXTURES_DIR / f"{name}.json").exists(), name


@pytest.mark.parametrize("name", _DATASET_NAMES)
def test_polars_backend_reproduces_frozen_fixture(name: str) -> None:
    canon, _wall, _rss = _mod.run_one(name, "polars")
    assert canon == _load_frozen(name)


@pytest.mark.parametrize("name", _DATASET_NAMES)
def test_arrow_backend_reproduces_frozen_fixture(name: str) -> None:
    canon, _wall, _rss = _mod.run_one(name, "arrow")
    assert canon == _load_frozen(name)
