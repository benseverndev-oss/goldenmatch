"""The dead-code allowlist and its rot guard."""

from __future__ import annotations

from pathlib import Path

from dead_code.static import all_modules

REPO = Path(__file__).resolve().parent.parent.parent
ALLOW = REPO / "parity" / "dead_code.allow"


def load_allowlist() -> set[str]:
    """Allowlisted module names, reasons stripped."""
    if not ALLOW.exists():
        return set()
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
