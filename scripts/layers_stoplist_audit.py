"""Audit which party qualifiers each domain pack's field-type stop-list swallows.

WHY THIS EXISTS. Layer detection puts every token of every field-type
``name_hints`` entry into a stop-list, so a token like ``account`` cannot open a
phantom party out of ``account_number``/``account_id``. The escape hatch is that
a **role declaration wins**: ``stop -= role_tokens``.

That makes any token which is *both* a plausible party qualifier *and* a
substring of some type hint invisible to detection unless a pack happens to
declare it as a role. It is not a hypothetical: ``claim`` was suppressed in
``healthcare`` and ``insurance`` by their ``claim_status`` / ``claim_number``
hints, which cost the FK ground-truth corpus two of its three exact-partition
misses (#2574). Fixing that one token raised detectable-convention accuracy from
92% to 100%.

This script answers the follow-up question -- **was ``claim`` one of many, or a
one-off?** -- before anyone widens the packs on a hunch. It reports the
suppressed set; it does NOT decide which entries deserve a role declaration.
That call is human, and the constraint that decides it is
``MIN_SPECIFICITY = 1.0``: a stop-list which suppresses a real party is better
than a role vocabulary which invents one.

Attribute tokens (``name``, ``id``, ``date``...) are excluded from the report.
They are suppressed by the kernel's universal ``ATTRIBUTE_TOKENS`` list rather
than by any pack's types, and suppressing them is the whole point -- they
describe a property of an entity, never the identity of one.

    python scripts/layers_stoplist_audit.py
    python scripts/layers_stoplist_audit.py --json
"""

from __future__ import annotations

import argparse
import json


def run() -> dict:
    """Return ``{"domains": {...}, "summary": {...}}``.

    Per domain: every token a schema could plausibly use as a party qualifier
    that this pack's field types suppress, with the hints responsible.
    """
    from goldencheck_types import list_domains, load_domain
    from infermap.detect import _tokens
    from infermap.layers import (
        _ATTRIBUTE_TOKENS,
        _MIN_QUALIFIER_LEN,
        _pack_inputs,
        _with_generic_roles,
    )

    domains: dict[str, dict] = {}
    for name in sorted(list_domains()):
        pack = load_domain(name)
        roles, type_hints = _pack_inputs(pack)
        roles = _with_generic_roles(roles)

        role_tokens = {
            tok for (_n, _k, hints, _t) in roles for hint in hints for tok in _tokens(hint)
        }

        # token -> the type hints that put it in the stop-list
        blamed: dict[str, set[str]] = {}
        for hint in type_hints:
            for tok in _tokens(hint):
                blamed.setdefault(tok, set()).add(hint)

        suppressed = {
            tok: sorted(hints)
            for tok, hints in blamed.items()
            if len(tok) >= _MIN_QUALIFIER_LEN
            and tok not in role_tokens
            and tok not in _ATTRIBUTE_TOKENS
        }
        domains[name] = {
            "n_types": len(pack.types),
            "n_roles": len(pack.roles),
            "suppressed": dict(sorted(suppressed.items())),
        }

    all_tokens = sorted({t for d in domains.values() for t in d["suppressed"]})
    return {
        "domains": domains,
        "summary": {
            "n_domains": len(domains),
            "n_distinct_suppressed_tokens": len(all_tokens),
            "distinct_suppressed_tokens": all_tokens,
        },
    }


def report(result: dict) -> None:
    print("=" * 76)
    print("PARTY QUALIFIERS SUPPRESSED BY FIELD-TYPE STOP-LISTS")
    print("  a token here can never open a party in this pack unless a role")
    print("  declaration is added for it -- `stop -= role_tokens` is the only escape")
    print("=" * 76)
    for name, d in result["domains"].items():
        suppressed = d["suppressed"]
        head = f"{name:16s} types={d['n_types']:2d} roles={d['n_roles']:2d}"
        if not suppressed:
            print(f"{head}  (none)")
            continue
        print(f"{head}  suppressed={len(suppressed)}")
        for tok, hints in suppressed.items():
            print(f"    {tok:22s} <- {', '.join(hints)}")
    s = result["summary"]
    print("-" * 76)
    print(f"  {s['n_distinct_suppressed_tokens']} distinct tokens across {s['n_domains']} packs")
    print("-" * 76)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit the raw result")
    args = ap.parse_args()

    result = run()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
