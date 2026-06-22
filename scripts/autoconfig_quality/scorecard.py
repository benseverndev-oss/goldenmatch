"""Scorecard assembly + stable JSON I/O.

The scorecard is one JSON artifact: per-dataset records under a metadata header.
Provenance is git_sha + native_version (NO wall-clock timestamp / RNG), so
re-runs on the same code are byte-stable and cleanly diffable. Floats are
rounded to a fixed precision so trivial ULP noise never churns the diff.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

_FLOAT_PRECISION = 6


def _round_floats(obj: Any) -> Any:
    """Recursively round every float for byte-stable serialization."""
    if isinstance(obj, float):
        return round(obj, _FLOAT_PRECISION)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def build_scorecard(
    results: dict[str, dict],
    *,
    native_version: str,
    git_sha: str,
    skipped: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble per-dataset records + a stable metadata header."""
    return {
        "meta": {
            "native_version": native_version,
            "git_sha": git_sha,
            "datasets_run": sorted(results.keys()),
            "datasets_skipped": skipped or {},
        },
        "datasets": _round_floats(results),
    }


def gather_meta() -> tuple[str, str]:
    """(native_version, git_sha) from the environment. Best-effort; never raises."""
    try:
        import goldenmatch_native  # noqa: PLC0415
        native_version = getattr(goldenmatch_native, "__version__", "unknown")
    except Exception:
        native_version = "absent"
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        git_sha = "unknown"
    return native_version, git_sha


def dumps(scorecard: dict) -> str:
    return json.dumps(scorecard, indent=2, sort_keys=True)


def loads(text: str) -> dict:
    return json.loads(text)
