"""CI lint: ``map_elements`` calls in prep-stage modules must carry a
``# noqa: GM-MAP-ELEMENTS: <rationale>`` justification comment.

GH issue #628 (Arrow-native roadmap Phase 6).

The roadmap's Phase 6 goal is to eliminate per-row Python work
(``map_elements`` lambda) from stages that run on the full df. Some
call sites genuinely need it (user-registered standardizers without
native equivalents, transforms like soundex/metaphone that need a
Rust kernel) but each one must be deliberate and documented.

This script enforces: any ``map_elements`` call in
``core/standardize.py``, ``core/matchkey.py``, ``core/domain.py``
must have a ``# noqa: GM-MAP-ELEMENTS:`` comment within 6 lines
above it (covers chained Polars expressions where the
``.map_elements(`` lands a few lines below the function-level
comment). Exits 1 if any unjustified call is found.

Usage::

    python scripts/check_map_elements.py

CI integrates via ``.github/workflows/ci.yml`` -- one extra step in
the lint job, ~50ms wall on the current 12 call sites.

Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
(gitignored, Phase 6 section).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Files where map_elements calls require justification. Phase 6 spec
# focuses on the prep-stage hot paths; tests and benches are excluded.
_GATED_FILES = [
    "packages/python/goldenmatch/goldenmatch/core/standardize.py",
    "packages/python/goldenmatch/goldenmatch/core/matchkey.py",
    "packages/python/goldenmatch/goldenmatch/core/domain.py",
]

# Marker that callers must include within 3 lines above a
# ``map_elements`` call. Format: ``# noqa: GM-MAP-ELEMENTS: <reason>``.
_NOQA_PATTERN = re.compile(r"#\s*noqa:\s*GM-MAP-ELEMENTS")

# A ``map_elements`` call. We match the bare token; misses are
# acceptable false-positives that the author can suppress via the
# noqa marker.
_CALL_PATTERN = re.compile(r"\.map_elements\s*\(")


def _check_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_no, source_line) for unjustified calls."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if _CALL_PATTERN.search(line):
            # Look back up to 6 lines for the noqa marker. Wider than
            # 3 because chained Polars expressions land the
            # ``.map_elements(`` token several lines below the
            # function-level rationale comment (see standardize.py's
            # ``pl.col(...).cast(...).map_elements(...).alias(...)``
            # builder pattern).
            window_start = max(0, i - 6)
            window = lines[window_start:i + 1]
            if not any(_NOQA_PATTERN.search(w) for w in window):
                violations.append((i + 1, line.rstrip()))
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    total_violations = 0
    for rel in _GATED_FILES:
        path = repo_root / rel
        violations = _check_file(path)
        for line_no, src in violations:
            print(
                f"{rel}:{line_no}: GM-MAP-ELEMENTS: "
                f"map_elements call requires `# noqa: GM-MAP-ELEMENTS: "
                f"<rationale>` within 3 lines above\n  {src}",
                file=sys.stderr,
            )
        total_violations += len(violations)

    if total_violations:
        print(
            f"\n{total_violations} unjustified map_elements call(s). "
            f"Each requires a `# noqa: GM-MAP-ELEMENTS: <rationale>` "
            f"comment explaining why a native Polars / Rust kernel "
            f"equivalent isn't yet available.",
            file=sys.stderr,
        )
        return 1
    print(f"check_map_elements: all {sum(1 for _ in _GATED_FILES)} gated "
          f"files clean (existing map_elements calls properly justified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
