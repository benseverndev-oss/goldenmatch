#!/usr/bin/env python3
"""Regenerate the ENTIRE derived-docs battery in one command.

Adding an MCP tool / CLI command / scorer / config knob / env var ripples into a
handful of committed, generated artifacts (config-matrix, agent-manifest,
agent-codemap, api-surface, suite-matrix, thesis-weaknesses, native docs) plus a
couple of hand-authored count figures (llms.txt / README / api-surface inline).
Forgetting even one reddens CI on a stale-doc gate. This is the single entry point
so nobody has to remember the list:

    python scripts/regen_docs.py         # regenerate everything (a.k.a. `make docs`)
    python scripts/regen_docs.py --check  # CI: regenerate, then fail if the tree drifted

`--check` is what the `docs_regen` CI job runs: it regenerates in place and then
fails (showing exactly what to commit) if anything was stale, and runs the two
non-regenerable prose-count checks. The staleness probe is `git status
--porcelain`, not `git diff`: diff sees only TRACKED changes, so a generator
emitting a brand-new page used to pass the gate with the file uncommitted. Run it under the synced
workspace (`uv run python scripts/regen_docs.py`) so every package is importable.

Some drift lives in hand-authored PROSE (the "97 tools" figures in llms.txt /
README / api-surface); no generator rewrites those, so this reports them for a
manual bump rather than silently passing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# Generators that WRITE a committed artifact. Order is irrelevant (each is
# independent); kept grouped for readability.
WRITE_STEPS: list[list[str]] = [
    ["scripts/gen_config_matrix.py", "--write"],      # docs-site/*/config-matrix.mdx
    ["scripts/gen_config_matrix.py", "--manifest"],   # docs/agent-manifest.json (+ goldensuite-mcp copy)
    ["scripts/agent_codemap.py", "--write"],          # docs/agent-codemap.json
    ["scripts/gen_api_surface.py", "--write"],        # docs-site/reference/api-surface.mdx (generated block)
    ["scripts/gen_suite_matrix.py", "--write"],       # docs-site/suite-matrix.mdx
    ["scripts/gen_thesis_weaknesses.py", "--write"],  # docs-site/thesis-weaknesses.mdx
    ["scripts/gen_native_docs.py", "--write"],        # docs-site/*/native.mdx
]

# Checks for HAND-AUTHORED figures that no --write regenerates (prose counts).
PROSE_CHECKS: list[list[str]] = [
    ["scripts/check_llms_counts.py"],                 # llms.txt / README / suite tool+export counts
    ["scripts/gen_api_surface.py", "--check"],        # api-surface.mdx inline figures (block already fresh)
]

# The ONLY paths a generator writes. The drift check is scoped to these so an
# unrelated CI-mutated tracked file (notably `uv.lock`, which `uv sync` re-resolves
# in the runner) can't false-trip the gate -- we assert the GENERATED DOCS are
# current, nothing else.
GENERATED_PATHS: list[str] = [
    "docs-site",                                              # config-matrix / api-surface / suite-matrix / thesis / native
    "docs/agent-manifest.json",
    "docs/agent-codemap.json",
    "packages/python/goldensuite-mcp/goldensuite_mcp/agent-manifest.json",
]


def _run(argv: list[str]) -> int:
    print(f"  $ {' '.join(argv)}")
    return subprocess.run([PY, *argv], cwd=ROOT).returncode


def _drifted_paths() -> list[str]:
    """Porcelain status lines for the GENERATED DOCS -- empty means no drift.

    `git status --porcelain` rather than `git diff --quiet`: diff reports only
    TRACKED modifications, so a generator emitting a BRAND-NEW page (the "onboard
    a package" case) left the gate green with the file uncommitted -- the drift
    this job exists to catch, invisible to it. Porcelain sees added, deleted and
    untracked alike.

    Still scoped to GENERATED_PATHS so an unrelated CI-mutated file (notably
    `uv.lock`, which `uv sync` re-resolves in the runner) can't false-trip it, and
    `--untracked-files=normal` keeps directory-level rollup for untracked trees.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal", "--", *GENERATED_PATHS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the derived-docs battery.")
    ap.add_argument("--check", action="store_true",
                    help="CI mode: regenerate, then fail if the working tree drifted.")
    args = ap.parse_args()

    print("Regenerating derived docs...")
    for step in WRITE_STEPS:
        if _run(step) != 0:
            print(f"\nERROR: generator failed: {' '.join(step)}", file=sys.stderr)
            return 1

    print("\nChecking hand-authored figures (not auto-regenerable)...")
    prose_failed = [c for c in PROSE_CHECKS if _run(c) != 0]

    if args.check:
        ok = True
        drifted = _drifted_paths()
        if drifted:
            print("\nDOC DRIFT: committed generated docs were stale. The changes below are "
                  "the fix -- run `python scripts/regen_docs.py` and commit them:\n",
                  file=sys.stderr)
            for line in drifted:
                print(f"  {line}", file=sys.stderr)
            print(file=sys.stderr)
            subprocess.run(["git", "--no-pager", "diff", "--stat", "--", *GENERATED_PATHS], cwd=ROOT)
            subprocess.run(["git", "--no-pager", "diff", "--", *GENERATED_PATHS], cwd=ROOT)
            ok = False
        if prose_failed:
            print("\nDOC DRIFT: hand-authored count figures are stale (see the checks above); "
                  "bump them by hand, then re-run.", file=sys.stderr)
            ok = False
        if ok:
            print("\nDocs are current -- nothing to regenerate.")
        return 0 if ok else 1

    # write mode (`make docs`): regenerated in place; surface any manual prose bump.
    if prose_failed:
        print("\nRegenerated the generated docs. NOTE: the prose-count checks above still fail "
              "-- bump the hand-authored figures, then commit.", file=sys.stderr)
        return 1
    print("\nDone. `git status` shows what was regenerated; commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
