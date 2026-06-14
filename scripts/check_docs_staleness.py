#!/usr/bin/env python3
"""Tier-2 documentation-staleness advisory: diff-aware doc-drift detector.

Given a git diff range (default ``origin/main..HEAD``), apply a small set of
HIGH-signal, LOW-false-positive rules that catch the most common "code changed
but its doc surface didn't" drift. Designed to run as an ADVISORY CI job
(``continue-on-error: true``) so it surfaces warnings/annotations without ever
blocking a clean PR -- with ONE exception that is high-signal enough to gate.

Rules
-----
1. flag rule (GATING):
   If the diff adds or removes a ``GOLDENMATCH_[A-Z0-9_]+`` env flag in
   ``packages/python/**/*.py``, the canonical flag reference
   ``docs-site/goldenmatch/tuning.mdx`` MUST also be in the diff. If not ->
   ``::error::`` annotation + exit 1. (Per .claude/doc-surfaces.md, tuning.mdx is
   the authoritative GOLDENMATCH_* reference; an added/removed flag is the single
   highest-signal doc drift.)

2. public-symbol rule (ADVISORY):
   If the diff changes a package ``__init__.py`` ``__all__`` / re-export and NO
   doc surface is touched (``docs-site/``, any ``README.md``, ``CHANGELOG.md``,
   ``llms.txt``, ``context-network/``) -> ``::warning::`` only (never fails).

Only the flag rule can change the exit code. Everything else is informational.

Run: ``python scripts/check_docs_staleness.py [--base <ref>] [--head <ref>]``
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUNING_MDX = "docs-site/goldenmatch/tuning.mdx"

_FLAG_RE = re.compile(r"GOLDENMATCH_[A-Z0-9_]+")
_ALL_LINE_RE = re.compile(r"__all__")


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", f"{base}...{head}")
    return [ln for ln in out.splitlines() if ln.strip()]


def diff_for(base: str, head: str, pathspec: list[str]) -> str:
    return _git("diff", "--unified=0", f"{base}...{head}", "--", *pathspec)


def _added_removed_flags(diff_text: str) -> tuple[set[str], set[str]]:
    """Flags appearing on added (+) vs removed (-) diff lines.

    A flag is considered *introduced/removed* only if it nets out: a flag that
    moves within a file (present on both + and - lines) is not drift.
    """
    added: set[str] = set()
    removed: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.update(_FLAG_RE.findall(line))
        elif line.startswith("-"):
            removed.update(_FLAG_RE.findall(line))
    net_added = added - removed
    net_removed = removed - added
    return net_added, net_removed


def check_flag_rule(base: str, head: str, files: list[str]) -> tuple[bool, list[str]]:
    """Return (ok, messages). ok=False means gate failure."""
    py_files = [f for f in files if f.startswith("packages/python/") and f.endswith(".py")]
    if not py_files:
        return True, ["flag rule: no packages/python/**/*.py changes -- skipped"]

    diff_text = diff_for(base, head, py_files)
    net_added, net_removed = _added_removed_flags(diff_text)
    touched_flags = sorted(net_added | net_removed)
    if not touched_flags:
        return True, ["flag rule: no GOLDENMATCH_* flag added/removed -- OK"]

    tuning_touched = TUNING_MDX in files
    if tuning_touched:
        return True, [
            f"flag rule: flags changed ({touched_flags}) and {TUNING_MDX} is in the "
            f"diff -- OK"
        ]
    # Drift: flag changed but tuning.mdx untouched.
    msgs = [
        f"::error file={TUNING_MDX}::GOLDENMATCH_* flag(s) "
        f"{touched_flags} added/removed in this diff but {TUNING_MDX} (the canonical "
        f"flag reference) was not updated. Add/remove the flag in tuning.mdx."
    ]
    return False, msgs


def check_public_symbol_rule(base: str, head: str, files: list[str]) -> list[str]:
    """Advisory only. Return ::warning:: messages (never affects exit code)."""
    init_files = [
        f for f in files
        if f.startswith("packages/") and f.endswith("__init__.py")
    ]
    if not init_files:
        return ["public-symbol rule: no __init__.py changes -- skipped"]

    diff_text = diff_for(base, head, init_files)
    changes_all = any(
        _ALL_LINE_RE.search(ln)
        for ln in diff_text.splitlines()
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
    )
    if not changes_all:
        return ["public-symbol rule: no __all__/re-export lines changed -- skipped"]

    doc_touched = any(
        f.startswith("docs-site/")
        or f.endswith("README.md")
        or f.endswith("CHANGELOG.md")
        or f.endswith("llms.txt")
        or f.startswith("context-network/")
        for f in files
    )
    if doc_touched:
        return ["public-symbol rule: __all__ changed and a doc surface was touched -- OK"]
    return [
        "::warning::A package __all__/re-export changed but no doc surface "
        "(docs-site/, README.md, CHANGELOG.md, llms.txt, context-network/) was "
        "updated. Public-API changes usually need a doc note. (advisory only)"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base ref (default origin/main).")
    parser.add_argument("--head", default="HEAD", help="Head ref (default HEAD).")
    args = parser.parse_args(argv)

    try:
        files = changed_files(args.base, args.head)
    except RuntimeError as exc:
        print(f"::warning::docs-staleness: could not compute diff ({exc}); skipping.")
        return 0

    print(f"Docs staleness advisory: {args.base}...{args.head} "
          f"({len(files)} changed file(s))")

    gate_ok, flag_msgs = check_flag_rule(args.base, args.head, files)
    symbol_msgs = check_public_symbol_rule(args.base, args.head, files)

    for m in flag_msgs + symbol_msgs:
        print(m)

    if not gate_ok:
        print("\nDocs staleness FAILED (flag rule). Update "
              f"{TUNING_MDX} in the same PR.")
        return 1
    print("\nDocs staleness OK (gating flag rule passed; symbol rule advisory only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
