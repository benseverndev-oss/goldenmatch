"""Report config fields whose readers span modules and must agree.

Report-only, by design. This proposes; a person disposes. See the spec's "Being
wrong": phase B's dangerous failure is a BAD MERGE -- collapsing two
implementations that must stay separate -- so nothing here remediates.

Ordering: the inventory is sorted by ACCESSOR COUNT ASCENDING, then by field
name for ties -- NOT alphabetically by field, despite what an earlier draft of
this module did. Measured against the real package: the scan finds 71 shared
fields, and sorting by name puts `field` (48 accessor modules) and `name` (44)
at the top while `passes`/`keys` -- the pair that actually shipped a silent
wrong answer, with 11 accessors -- sits far below. A field touched by 48
modules is a universal accessor; the divergence risk this inventory exists to
surface is highest where FEW accessors make a NON-TRIVIAL, easy-to-diverge-on
choice, so fewer accessors sort first. Nothing is dropped -- every shared,
un-allowlisted field is still here -- only ranked, so the decision-shaped
entries lead and the universal accessors sink to the bottom.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shared_decisions.allowlist import load_allowlist, stale_entries
from shared_decisions.readers import shared_fields

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"


def inventory(root: Path) -> list[dict]:
    """Shared fields minus the declared-agreement allowlist.

    Sorted by accessor count ascending, then field name -- see the module
    docstring for why. The return shape (`list[dict]` with `field`/`readers`
    keys) is unchanged by the ranking: only the order of the list differs.
    """
    shared = shared_fields(root)
    allowed = load_allowlist()
    items = [
        {"field": f, "readers": sorted(mods)} for f, mods in shared.items() if f not in allowed
    ]
    items.sort(key=lambda item: (len(item["readers"]), item["field"]))
    return items


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    items = inventory(args.root)
    shared = shared_fields(args.root)
    stale = stale_entries(set(shared))

    if args.json:
        print(json.dumps({"inventory": items, "stale_allowlist_entries": sorted(stale)}, indent=2))
        return 1 if stale else 0

    print(
        f"{len(shared)} config field(s) read by more than one module; "
        f"{len(items)} not yet recorded as agreed"
    )
    print()
    for item in items:
        print(f"  {item['field']}  ({len(item['readers'])} readers)")
        for m in item["readers"]:
            print(f"      {m}")
    if stale:
        print()
        print(f"STALE allowlist entries (no longer a shared field): {sorted(stale)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
