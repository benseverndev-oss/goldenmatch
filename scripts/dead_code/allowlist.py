"""The dead-code allowlist and its rot guard.

Reads the curated per-package classification maps at parity/dead_code/*.yaml --
the SAME maps check_dead_code.py's own `dead_code_deferred` mechanism consumes
(spec: docs/superpowers/specs/2026-07-22-arch-aware-dead-code-detection.md) --
rather than the parallel parity/dead_code.allow file this module used to read.
One curated source per package, reused by both detectors, not two that can
drift apart.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dead_code.static import all_modules

REPO = Path(__file__).resolve().parent.parent.parent
ALLOW_DIR = REPO / "parity" / "dead_code"


def _allow_files() -> list[Path]:
    return sorted(ALLOW_DIR.glob("*.yaml"))


def load_allowlist() -> set[str]:
    """Allowlisted module names, union across every parity/dead_code/*.yaml map.

    Each file is a flat `module: "reason"` mapping (plus `#` comments); only the
    module names are returned, reasons are dropped.

    Raises FileNotFoundError if the source directory is missing or holds no yaml
    maps, to prevent silent deletion of live integrations (MongoDB, BigQuery,
    HubSpot, Vertex AI, Alembic migrations, connector dynamic dispatch, ...)
    that sit at 0% coverage only because CI lacks credentials or because they
    are reached out-of-band. A missing source is a hard failure, not a safe
    default.
    """
    files = _allow_files()
    if not ALLOW_DIR.is_dir() or not files:
        raise FileNotFoundError(
            f"Dead-code allowlist source not found: {ALLOW_DIR} (no *.yaml maps).\n"
            f"These files document live integrations that cannot run in CI.\n"
            f"A missing allowlist is not a safe default — it silently allows\n"
            f"Task 7 to delete working code with every test still green."
        )
    out: set[str] = set()
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        out.update(data.keys())
    return out


def stale_entries() -> set[str]:
    """Allowlisted modules that no longer exist.

    A stale entry can never match, so it silently shrinks the audit while
    reading as documentation.
    """
    return load_allowlist() - all_modules()
