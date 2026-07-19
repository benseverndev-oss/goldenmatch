"""Generate (or check) the per-package config-matrix docs from the live surface.

  python scripts/gen_config_matrix.py --write [pkg|all]    # regenerate the block(s)
  python scripts/gen_config_matrix.py --check [pkg|all]     # exit 1 if any is stale
  python scripts/gen_config_matrix.py --refs  [pkg|all]     # exit 1 if a doc names a dead env knob OR omits a canonical scorer/strategy/...
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
from config_matrix.crossref import stale_env_refs, undocumented_vocab
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
            env_hits = stale_env_refs(REGISTRY[name])
            doc_gaps = undocumented_vocab(REGISTRY[name])
            bad += env_hits + doc_gaps
            for h in env_hits:
                print(f"::error file={h.page},line={h.line_no}::{h.token} is documented "
                      f"in {h.page} but is not read anywhere in {name}'s source "
                      f"(removed/renamed knob?). Fix the doc or add to env_allow.",
                      file=sys.stderr)
            for h in doc_gaps:
                print(f"::error file={h.page}::{h.token} is a live {name} config value "
                      f"but is not documented in {h.page}. Add it (propagate the new knob "
                      f"to its reference doc).", file=sys.stderr)
            if not env_hits and not doc_gaps:
                print(f"OK    {name}: docs reference only live knobs + cover the canonical set")
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
