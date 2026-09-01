"""The dead-code allowlist and its rot guard."""

from __future__ import annotations

from pathlib import Path

from dead_code.static import all_modules

REPO = Path(__file__).resolve().parent.parent.parent
ALLOW = REPO / "parity" / "dead_code.allow"


def load_allowlist() -> set[str]:
    """Allowlisted module names, reasons stripped.

    Raises FileNotFoundError if the allowlist file is missing, to prevent
    silent deletion of live integrations (MongoDB, BigQuery, HubSpot, Vertex AI)
    that sit at 0% coverage only because CI lacks credentials. A missing file
    is a hard failure, not a safe default.
    """
    if not ALLOW.exists():
        raise FileNotFoundError(
            f"Dead-code allowlist not found: {ALLOW}\n"
            f"This file documents live integrations that cannot run in CI.\n"
            f"A missing allowlist is not a safe default — it silently allows\n"
            f"Task 7 to delete working code with every test still green."
        )
    out: set[str] = set()
    for line in ALLOW.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            out.add(entry)
    return out


def stale_entries() -> set[str]:
    """Allowlisted modules that no longer exist.

    A stale entry can never match, so it silently shrinks the audit while
    reading as documentation.
    """
    return load_allowlist() - all_modules()
