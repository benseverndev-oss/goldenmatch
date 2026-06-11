#!/usr/bin/env python3
"""TS cross-language parity-fixture staleness gate (#856).

The cross-language parity harness validates the TypeScript port against
*committed JSON fixtures*, never against *current Python*, and nothing kept the
fixtures fresh -- so a pure-Python behaviour change left the committed vectors
stale while the TS parity test stayed green against them ("TS matches the
fixture", never "TS matches Python today").

This gate closes that hole. Run AFTER ``scripts/regen_ts_parity_fixtures.sh``
(which regenerates every committed fixture in place from the Python emitters):
it compares the working tree against ``git HEAD`` with a FLOAT-TOLERANT JSON
diff and fails if any non-allowlisted fixture drifted beyond tolerance.

Tolerance: numeric leaves compare within ``FLOAT_TOL`` (the 4-decimal scorer
parity contract); everything else must match exactly. This absorbs last-bit
float noise (``0.19999999999999998`` vs ``0.20000000000000004``) -- which would
make a naive byte-exact ``git diff`` gate flaky -- while still catching
structural or real value drift.

Known divergences (allowlisted, NOT compared): see ``ALLOWLIST`` below.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 4-decimal scorer-parity contract (tests/parity/scorer-ground-truth.test.ts
# asserts ``toBeCloseTo(expected, 4)``); match it here so the gate's notion of
# "equal" is the same as the TS parity tests'.
FLOAT_TOL = 1e-4

# Metadata-stamp keys excluded from the comparison anywhere they appear. These
# record HOW a fixture was generated, not the cross-language behaviour being
# asserted -- comparing them would fail the gate on every release with zero
# behavioural drift (e.g. goldenpipe's `pipe_parity.json` stamps the producing
# `python_version`, which the TS test declares but does not assert against TS
# output).
IGNORE_KEYS = frozenset({"python_version"})

# Directories holding committed TS cross-language parity fixtures.
FIXTURE_DIRS = [
    "packages/typescript/goldenmatch/tests/parity",
    "packages/typescript/goldenpipe/tests/fixtures",
]

# Known divergences: committed fixtures the gate does NOT compare, each with a
# reason. These are produced by EXECUTING the auto-config controller, whose
# output depends on which optional deps are importable at runtime (the
# polars/sklearn import paths documented in
# controller-stoppoint.parity.test.ts) and is therefore not deterministically
# reproducible across environments. They keep their existing TS-vs-committed
# parity test instead. Pinning a canonical generation environment so these can
# be gated too is a tracked #856 follow-up.
ALLOWLIST = {
    "controller-stoppoint-fixtures.json": (
        "auto-config controller execution is optional-dependency-sensitive; "
        "not deterministically reproducible across environments (#856)"
    ),
    "autoconfig-verify-fixtures.json": (
        "auto_configure_df execution is optional-dependency-sensitive; "
        "not deterministically reproducible across environments (#856)"
    ),
}


def _git_head_text(relpath: str) -> str | None:
    """Committed contents of ``relpath`` at HEAD, or None if untracked there."""
    res = subprocess.run(
        ["git", "show", f"HEAD:{relpath}"], capture_output=True, text=True
    )
    return res.stdout if res.returncode == 0 else None


def _tracked_fixtures() -> list[str]:
    res = subprocess.run(
        ["git", "ls-files", "--", *FIXTURE_DIRS],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        line.strip()
        for line in res.stdout.splitlines()
        if line.strip().endswith(".json")
    ]


def _num_close(a: float, b: float) -> bool:
    return abs(a - b) <= FLOAT_TOL + FLOAT_TOL * max(abs(a), abs(b))


def _diff(committed, current, path: str = "$"):
    """Yield human-readable drift descriptions (empty => fresh within tol)."""
    # bool is a subclass of int -- compare it before the numeric branch.
    if isinstance(committed, bool) or isinstance(current, bool):
        if committed != current:
            yield f"{path}: {committed!r} != {current!r}"
        return
    if isinstance(committed, (int, float)) and isinstance(current, (int, float)):
        if not _num_close(float(committed), float(current)):
            yield f"{path}: {committed!r} != {current!r}"
        return
    if type(committed) is not type(current):
        yield (
            f"{path}: type {type(committed).__name__} != "
            f"{type(current).__name__}"
        )
        return
    if isinstance(committed, dict):
        for key in sorted(set(committed) | set(current)):
            if key in IGNORE_KEYS:
                continue
            if key not in committed:
                yield f"{path}.{key}: new key not in committed fixture"
            elif key not in current:
                yield f"{path}.{key}: dropped by regeneration"
            else:
                yield from _diff(committed[key], current[key], f"{path}.{key}")
        return
    if isinstance(committed, list):
        if len(committed) != len(current):
            yield f"{path}: list length {len(committed)} != {len(current)}"
            return
        for i, (c, d) in enumerate(zip(committed, current)):
            yield from _diff(c, d, f"{path}[{i}]")
        return
    if committed != current:
        yield f"{path}: {committed!r} != {current!r}"


def main() -> int:
    fixtures = _tracked_fixtures()
    drifted: dict[str, list[str]] = {}
    missing: list[str] = []
    allowlisted: list[str] = []

    for rel in fixtures:
        name = Path(rel).name
        if name in ALLOWLIST:
            allowlisted.append(rel)
            continue
        head = _git_head_text(rel)
        if head is None:
            # Brand-new fixture not yet in HEAD -- nothing to compare against.
            continue
        wt_path = Path(rel)
        if not wt_path.exists():
            missing.append(rel)
            continue
        try:
            committed = json.loads(head)
            current = json.loads(wt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            drifted[rel] = [f"JSON parse error: {exc}"]
            continue
        deltas = list(_diff(committed, current))
        if deltas:
            drifted[rel] = deltas

    print(
        f"checked {len(fixtures) - len(allowlisted)} fixture(s) "
        f"({len(allowlisted)} allowlisted, float tol={FLOAT_TOL})"
    )
    for rel in allowlisted:
        print(f"  ~ allowlisted {rel}\n      reason: {ALLOWLIST[Path(rel).name]}")

    if not drifted and not missing:
        print("OK: all gated TS parity fixtures are fresh (within tolerance).")
        return 0

    print()
    print(
        "::error::TS parity fixtures are STALE. Run "
        "`scripts/regen_ts_parity_fixtures.sh` and commit the result "
        "(or add an intentional divergence to ALLOWLIST in this script):"
    )
    for rel, deltas in drifted.items():
        print(f"  DRIFT  {rel}")
        for line in deltas[:8]:
            print(f"      {line}")
        if len(deltas) > 8:
            print(f"      ... and {len(deltas) - 8} more")
    for rel in missing:
        print(f"  MISSING after regeneration: {rel}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
