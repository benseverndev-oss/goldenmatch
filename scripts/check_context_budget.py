#!/usr/bin/env python3
"""Cap the size of the CLAUDE.md files, which are loaded into every session.

WHY THIS EXISTS
---------------
#2445 and #2446 cut the context loaded before any goldenmatch work from 259,621
chars (~64,900 tokens) to 42,859 (~10,700) -- an 84% reduction. That was a
one-time cleanup against a standing habit: in the 30 days before it, the root
file alone took +208 / -5 lines. A 40:1 append ratio.

Nothing in that cleanup stops the regrowth. Without a ceiling, the same work is
due again in six months, and the reason is structural rather than careless: each
individual addition is genuinely useful to the person adding it, and the cost is
paid by everyone else, later, invisibly. A budget makes the tradeoff explicit at
the moment it is taken -- you may add, but you must fold or move something first.

RATCHET, NOT ASPIRATION
-----------------------
Budgets are set just above each file's CURRENT size, so this is non-breaking
today and only ever bites on growth. When a file shrinks well below its budget
the gate says so and suggests the tightened number; it does NOT fail, because a
gate that punishes an improvement gets switched off.

Raising a budget is legitimate -- some things genuinely belong in always-loaded
context -- but it is a visible line in a diff, which is the entire point. Before
raising one, apply the Tier-1 test: would an agent do the wrong thing without
this, on a change ANYWHERE in this file's scope? If it is "only when touching X",
it belongs next to X.

FAIL (exit 1) on an over-budget file, or on a manifest entry naming a file that
no longer exists. Exit 2 if the scan looks broken.

Run from the repo root:  python scripts/check_context_budget.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Bytes. Set from the size at the time of writing plus modest headroom.
# A file not listed here gets DEFAULT_BUDGET; crossing that means adding an
# explicit entry, which is itself the decision this gate exists to surface.
BUDGETS: dict[str, int] = {
    # Loaded in EVERY session in every package -- the strictest budget in the repo.
    "CLAUDE.md": 21_000,
    "packages/python/CLAUDE.md": 3_000,
    # Package-level files, loaded whenever that package is touched.
    "packages/python/goldenmatch/CLAUDE.md": 24_500,
    "packages/typescript/goldenmatch/CLAUDE.md": 65_000,
    "packages/python/goldenflow/CLAUDE.md": 46_000,
    "packages/rust/extensions/CLAUDE.md": 39_000,
    "packages/python/goldengraph/CLAUDE.md": 36_000,
    "packages/python/goldencheck/CLAUDE.md": 31_000,
    "packages/python/goldenpipe/CLAUDE.md": 19_000,
    "packages/python/infermap/CLAUDE.md": 9_000,
}

# Anything not named above. Nested subsystem files are all well under this.
DEFAULT_BUDGET = 10_000

# Report (do not fail) when a file has shrunk this far below its budget, so the
# ratchet can be tightened in the PR that did the shrinking.
SLACK_REPORT_THRESHOLD = 0.25

MIN_EXPECTED_FILES = 10


def claude_files() -> list[pathlib.Path]:
    return sorted(
        p
        for p in ROOT.rglob("CLAUDE.md")
        if "node_modules" not in p.parts and "_archive" not in p.parts
    )


def budget_for(rel: str) -> int:
    return BUDGETS.get(rel, DEFAULT_BUDGET)


def check() -> tuple[list[str], list[str], int]:
    """Return (failures, notes, files_scanned)."""
    failures: list[str] = []
    notes: list[str] = []

    files = claude_files()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        size = path.stat().st_size
        budget = budget_for(rel)
        if size > budget:
            over = size - budget
            explicit = rel in BUDGETS
            failures.append(
                f"::error file={rel}::{rel}: {size:,} bytes exceeds its "
                f"{budget:,}-byte budget by {over:,}. Fold or move {over:,} bytes "
                f"out (see context-network/operations/ and the package docs/context/ "
                f"dirs for where extracted detail lives), or raise the budget in "
                f"scripts/check_context_budget.py"
                + ("" if explicit else " by adding an explicit entry")
                + " and say why in the PR."
            )
        elif size < budget * (1 - SLACK_REPORT_THRESHOLD) and rel in BUDGETS:
            suggested = ((size * 11 // 10) // 500 + 1) * 500
            notes.append(
                f"{rel}: {size:,} bytes against a {budget:,} budget -- consider "
                f"tightening the ratchet to {suggested:,}"
            )

    for rel in BUDGETS:
        if not (ROOT / rel).exists():
            failures.append(
                f"::error::{rel} has a budget entry but does not exist -- remove the "
                f"stale entry from BUDGETS (a manifest that names missing files "
                f"cannot be trusted to name the present ones)"
            )

    return failures, notes, len(files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--show", action="store_true", help="print every file with its budget and headroom"
    )
    args = ap.parse_args(argv)

    failures, notes, scanned = check()

    if scanned < MIN_EXPECTED_FILES:
        print(
            f"::error::found only {scanned} CLAUDE.md file(s) -- below the "
            f"{MIN_EXPECTED_FILES} floor, so the scan is broken, not clean",
            file=sys.stderr,
        )
        return 2

    if args.show:
        for path in claude_files():
            rel = path.relative_to(ROOT).as_posix()
            size = path.stat().st_size
            budget = budget_for(rel)
            flag = "OVER" if size > budget else f"{100 * size // budget:>3}%"
            print(f"  {flag}  {size:>7,} / {budget:>7,}  {rel}")

    total = sum(p.stat().st_size for p in claude_files())
    print(f"context budget: {scanned} CLAUDE.md file(s), {total:,} bytes total")

    for note in notes:
        print(f"  note: {note}")

    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        print(
            f"\n{len(failures)} budget violation(s). These files are loaded into every "
            f"session in their scope, so bytes here are a tax on all of that work.",
            file=sys.stderr,
        )
        return 1

    print("  OK -- every CLAUDE.md is within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
