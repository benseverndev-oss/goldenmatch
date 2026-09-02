"""Report config fields whose accessors span modules and must agree.

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
from shared_decisions.fields import config_fields
from shared_decisions.readers import shared_fields, unparseable_modules

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch"

# What this scan can and cannot see, printed verbatim in the report header so
# silence here is never mistaken for a clean bill -- see finding 6.
# `fields.py` reads field names from ONE file, config/schemas.py, so a
# BaseModel declared elsewhere (web/settings.py, for one) contributes no
# field names to look for in the first place; and `DEFAULT_ROOT` walks only
# packages/python/goldenmatch/goldenmatch, so an accessor in scripts/,
# goldenflow, or the TypeScript port is out of reach by construction, not
# because it was checked and found clean.
SCOPE_NOTE = (
    "scope: field names come from packages/python/goldenmatch/goldenmatch/"
    "config/schemas.py only (web/settings.py's BaseModels are not read); "
    "accessors are scanned under {root} only -- scripts/, goldenflow, and "
    "the TypeScript port are out of reach by construction, and their "
    "silence here is not a clean bill"
)


def _declaring_classes(field: str, fields_by_class: dict[str, set[str]]) -> list[str]:
    """Every config class that declares `field`, sorted.

    A bare field name can be declared on more than one class (`strategy` on
    `BlockingConfig`, `GoldenFieldRule`, `GoldenGroupRule`) and
    `field_accessors` keys by name alone -- it has no type information to
    tell which class a given access actually resolves to. Full
    disambiguation needs type inference and is out of scope; this surfaces
    the ambiguity instead, see the report's DECLARED ON marker.
    """
    return sorted(cls for cls, names in fields_by_class.items() if field in names)


def inventory(root: Path, shared: dict[str, set[str]] | None = None) -> list[dict]:
    """Shared fields minus the declared-agreement allowlist.

    Sorted by accessor count descending, then field name ascending for ties
    -- see the module docstring for why. The return shape (`list[dict]` with
    `field`/`accessors`/`declared_on` keys) is unchanged by the ranking:
    only the order of the list differs.

    `shared` is injectable so a caller that already computed
    `shared_fields(root)` (main(), for one) doesn't pay to re-parse the whole
    tree a second time -- shared_fields is pure, so the two calls always
    agreed, it was just double the CI cost for nothing.
    """
    if shared is None:
        shared = shared_fields(root)
    allowed = load_allowlist()
    fields_by_class = config_fields()
    items = [
        {
            "field": f,
            "accessors": sorted(mods),
            "declared_on": _declaring_classes(f, fields_by_class),
        }
        for f, mods in shared.items()
        if f not in allowed
    ]
    items.sort(key=lambda item: (-len(item["accessors"]), item["field"]))
    return items


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    shared = shared_fields(args.root)  # computed ONCE, passed to inventory()
    items = inventory(args.root, shared=shared)
    # The allowlist describes DEFAULT_ROOT's field population, so staleness is
    # judged against THAT population -- never against whatever `--root` was
    # asked to report on. Comparing against a custom root (a test fixture, or
    # one package of several) reported nearly every entry as stale and exited
    # 1, because those fields simply are not in that tree. This stayed
    # invisible for as long as the allowlist was empty: an empty set has no
    # stale members whatever it is compared against, so it surfaced only when
    # B1 populated it.
    stale = stale_entries(
        set(shared) if args.root == DEFAULT_ROOT else set(shared_fields(DEFAULT_ROOT))
    )
    skipped = unparseable_modules(args.root)

    if args.json:
        print(
            json.dumps(
                {
                    "inventory": items,
                    "stale_allowlist_entries": sorted(stale),
                    "unparseable_modules": skipped,
                },
                indent=2,
            )
        )
        return 1 if stale else 0

    print(SCOPE_NOTE.format(root=args.root))
    print()
    print(
        f"{len(shared)} config field(s) accessed by more than one module; "
        f"{len(items)} not yet recorded as agreed"
    )
    if skipped:
        print(
            f"{len(skipped)} module(s) could not be parsed and are invisible "
            f"to every count above: {skipped}"
        )
    print()
    for item in items:
        marker = ""
        if len(item["declared_on"]) > 1:
            marker = (
                f"  -- DECLARED ON {len(item['declared_on'])} CLASSES: "
                f"{', '.join(item['declared_on'])} -- accessors may not refer "
                "to the same field"
            )
        print(f"  {item['field']}  ({len(item['accessors'])} accessors){marker}")
        for m in item["accessors"]:
            print(f"      {m}")
    if stale:
        print()
        print(f"STALE allowlist entries (no longer a shared field): {sorted(stale)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
