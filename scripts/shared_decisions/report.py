"""Report config fields whose readers span modules and must agree.

Report-only, by design. This proposes; a person disposes. See the spec's "Being
wrong": phase B's dangerous failure is a BAD MERGE -- collapsing two
implementations that must stay separate -- so nothing here remediates.

Ordering: the inventory is sorted by ACCESSOR COUNT DESCENDING, then by field
name for ties. An earlier draft sorted ascending, on the theory that few
accessors making a non-trivial choice carries the highest divergence risk.
That theory was never checked against the incident it was meant to surface,
and it was backwards: ascending buried `passes` and `keys` -- the pair that
actually shipped a silent wrong answer, 0 pairs where legacy produced 242 --
at ranks 64 and 70 of 71. The incident's real shape is a WIDELY-shared field:
more accessors means more modules that have to agree, hence more chances to
disagree, not fewer. Descending order puts `passes`, `keys`, and `strategy`
all in the top 8. This ranking is validated against the incident's own
position in the real inventory, not argued from first principles -- see
test_known_incident_fields_rank_near_the_top, which pins it to that evidence
rather than to a rule. Nothing is dropped -- every shared, un-allowlisted
field is still here -- only ranked, so the highest-blast-radius entries lead.
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

    Sorted by accessor count descending, then field name ascending for ties
    -- see the module docstring for why. The return shape (`list[dict]` with
    `field`/`readers` keys) is unchanged by the ranking: only the order of
    the list differs.
    """
    shared = shared_fields(root)
    allowed = load_allowlist()
    items = [
        {"field": f, "readers": sorted(mods)} for f, mods in shared.items() if f not in allowed
    ]
    items.sort(key=lambda item: (-len(item["readers"]), item["field"]))
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
