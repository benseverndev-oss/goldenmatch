#!/usr/bin/env python3
"""Report which declared dependencies have no upper bound, and why that matters.

This is a REPORT, not a gate, and deliberately so. Capping every dependency is a
known packaging anti-pattern: a library that pins `<N` on everything forces
resolution conflicts on anyone who depends on it, and the cost lands on users
who cannot edit our metadata. 187 of 195 requirements here are uncapped and
most of them should stay that way.

What actually broke on 2026-08-21 was not the absence of a ceiling. It was that
CI resolves through `uv.lock` (pinning mcp 1.28.1) while the Docker image and
every `pip install` resolve fresh (getting mcp 2.0.0). The lockfile made CI
structurally immune to precisely the drift that reaches production. The fix for
that is the unlocked-resolution lane, not 187 pins.

So this script exists to make the surface visible and to tell the two cases
apart:

  * a dependency whose PUBLIC, documented API we use  -> leave uncapped; a major
    bump is upstream's problem to communicate and ours to adapt to.
  * a dependency whose object model we reach into     -> a major bump WILL break
    us silently. `mcp` was this: we read `Tool.inputSchema`, 2.0 renamed the
    field and kept the old name as a pydantic alias, so every construction site
    kept working and only the reads failed -- at server startup.

Usage:
    python scripts/audit_dep_ceilings.py            # report
    python scripts/audit_dep_ceilings.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_REQ = re.compile(r'"([A-Za-z0-9_.\-]+)\s*((?:[<>=!~][^"]*)?)"')


def declared_requirements() -> list[dict]:
    """Every version-pinned requirement string across the Python packages."""
    out: list[dict] = []
    for pyproject in sorted((ROOT / "packages" / "python").glob("*/pyproject.toml")):
        text = pyproject.read_text(encoding="utf-8")
        for m in _REQ.finditer(text):
            name, spec = m.group(1), m.group(2).strip()
            if not spec or not spec[0] in "<>=!~":
                continue
            if name.lower() == "python":
                continue
            out.append({
                "package": pyproject.parent.name,
                "requirement": name,
                "spec": spec,
                "capped": "<" in spec,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reqs = declared_requirements()
    capped = [r for r in reqs if r["capped"]]
    uncapped = [r for r in reqs if not r["capped"]]

    if args.json:
        json.dump({"total": len(reqs), "capped": capped, "uncapped": uncapped},
                  sys.stdout, indent=2)
        print()
        return 0

    print(f"declared requirements: {len(reqs)}")
    print(f"  with an upper bound: {len(capped)}")
    print(f"  without:             {len(uncapped)}")
    print()
    print("CAPPED (each of these should say why, in a comment next to it):")
    for r in sorted(capped, key=lambda r: (r["requirement"], r["package"])):
        print(f"  {r['requirement']:<28} {r['spec']:<18} {r['package']}")
    print()
    by_req: dict[str, list[str]] = {}
    for r in uncapped:
        by_req.setdefault(r["requirement"], []).append(r["package"])
    print(f"UNCAPPED ({len(by_req)} distinct):")
    for name in sorted(by_req):
        pkgs = ", ".join(sorted(by_req[name]))
        print(f"  {name:<28} {pkgs}")
    print()
    print("Uncapped is the correct DEFAULT. Cap one only when we read into its")
    print("object model rather than its documented API -- see the module docstring.")
    print("The lane that actually catches a breaking major is `unlocked_resolution`")
    print("in ci.yml, which resolves the way users do instead of through uv.lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
