#!/usr/bin/env python3
"""Tier-3 release-sweep gate: has a docs sweep been run for the current release?

A "docs sweep" is the manual/skill-driven pass (the ``rollout-docs-sweep`` skill,
guided by ``.claude/doc-surfaces.md``) that updates the PROSE doc surfaces -- the
feature paragraphs, tuning descriptions, llms.txt capability counts -- that no
deterministic gate can author. Tier-1 / Tier-2 catch STRUCTURAL drift; this gate
catches "we cut a release but never swept the docs".

It compares the marker in ``docs/.docs-sweep.json`` against the current
``packages/python/goldenmatch`` version. They match => a sweep has been recorded
for this version => exit 0. They differ => the headline package was bumped since
the last recorded sweep => exit 1 with instructions.

This is intended to run on RELEASE / version-bump (NOT on every PR): it would
otherwise red every feature PR that bumps the version before the sweep happens.
Wire it into the publish/release workflow, or run it manually before tagging.

Run: ``python scripts/check_docs_sweep.py``
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ROOT / "docs" / ".docs-sweep.json"
GM_PYPROJECT = ROOT / "packages" / "python" / "goldenmatch" / "pyproject.toml"


def current_goldenmatch_version() -> str:
    return tomllib.loads(GM_PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def main() -> int:
    gm_version = current_goldenmatch_version()

    if not MARKER.exists():
        print(f"Docs-sweep gate FAILED: {MARKER.relative_to(ROOT)} is missing.")
        print("Run the rollout-docs-sweep skill, then create the marker with the "
              f"current goldenmatch version ({gm_version}).")
        return 1

    try:
        marker = json.loads(MARKER.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Docs-sweep gate FAILED: {MARKER.relative_to(ROOT)} is not valid JSON ({exc}).")
        return 1

    marked = marker.get("version")
    if marked == gm_version:
        print(f"Docs-sweep gate OK: marker version {marked} == goldenmatch {gm_version}.")
        return 0

    print(f"Docs-sweep gate FAILED: marker version {marked!r} != goldenmatch "
          f"{gm_version!r}.")
    print("goldenmatch was bumped since the last recorded docs sweep. Run the "
          "rollout-docs-sweep skill to update the prose doc surfaces, then bump "
          f"docs/.docs-sweep.json `version` to {gm_version} (and refresh commit/date).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
