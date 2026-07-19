"""Generate (or check) the per-package config-matrix docs from the live surface.

  python scripts/gen_config_matrix.py --write [pkg|all]    # regenerate the block(s)
  python scripts/gen_config_matrix.py --check [pkg|all]     # exit 1 if any is stale
  python scripts/gen_config_matrix.py --refs  [pkg|all]     # exit 1 if a prose doc names a dead env knob
  python scripts/gen_config_matrix.py --coverage [pkg|all]  # report NL-explanation coverage per package

Each suite package's config surface (pydantic tree / constructor kwargs / vocab
constants / <PREFIX>_* env scan) is the source of truth; the committed page's
generated block must match a fresh render. CI runs --check per package, so adding
or removing a config knob without regenerating turns the merge queue red.
"""
from __future__ import annotations

import sys

from config_matrix import REGISTRY
from config_matrix.coverage import coverage, format_report
from config_matrix.crossref import stale_env_refs
from config_matrix.render import docs_are_current, write_docs


def _targets(argv: list[str]) -> list[str]:
    names = [a for a in argv if not a.startswith("-")]
    if not names or names == ["all"]:
        return list(REGISTRY)
    for n in names:
        if n not in REGISTRY:
            raise SystemExit(f"unknown package '{n}'; known: {', '.join(REGISTRY)}")
    return names


def main(argv: list[str]) -> int:
    if "--check" in argv:
        stale = []
        for name in _targets(argv):
            spec = REGISTRY[name]
            if docs_are_current(spec):
                print(f"OK    {name}: {spec.doc_path}")
            else:
                stale.append(spec)
                print(f"STALE {name}: {spec.doc_path}", file=sys.stderr)
        if stale:
            names = " ".join(s.name for s in stale)
            print(f"::error::config matrix stale for: {names}. "
                  f"Run: python scripts/gen_config_matrix.py --write {names}", file=sys.stderr)
            return 1
        return 0
    if "--refs" in argv:
        bad = []
        for name in _targets(argv):
            hits = stale_env_refs(REGISTRY[name])
            bad += hits
            if hits:
                for h in hits:
                    print(f"::error file={h.page},line={h.line_no}::{h.token} is documented "
                          f"in {h.page} but is not read anywhere in {name}'s source "
                          f"(removed/renamed knob?). Fix the doc or add to env_allow.",
                          file=sys.stderr)
            else:
                print(f"OK    {name}: docs reference only live env knobs")
        return 1 if bad else 0
    if "--coverage" in argv:
        for name in _targets(argv):
            print(format_report(name, coverage(REGISTRY[name])))
        return 0
    if "--write" in argv:
        for name in _targets(argv):
            print(f"wrote {write_docs(REGISTRY[name])}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
