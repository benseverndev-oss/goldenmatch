"""Fields whose multi-module readers are known to agree."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ALLOWLIST = REPO / "parity" / "shared_decisions.allow"


def load_allowlist() -> set[str]:
    """Field names recorded as agreed.

    RAISES if the file is missing rather than returning an empty set: a silently
    empty allowlist turns every downstream comparison vacuous while every test
    stays green.
    """
    if not ALLOWLIST.exists():
        raise FileNotFoundError(f"allowlist missing: {ALLOWLIST}")
    out: set[str] = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.split("#", 1)[0].strip())
    return out


def entries_missing_reasons(lines: list[str]) -> list[str]:
    """Allowlist entries carrying no `# reason`.

    Takes lines rather than reading the file so the rule is testable against a
    synthetic entry: the shipped allowlist is empty at B0a, and a check that
    only ever runs over an empty file passes whatever the rule says.
    """
    bad: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            bad.append(line)
    return bad


def stale_entries(known: set[str]) -> set[str]:
    """Allowlisted names that no longer name a real shared field."""
    return load_allowlist() - known
