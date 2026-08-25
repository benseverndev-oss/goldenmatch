"""How often is layer detection blind *because names carry nothing*?

WHY THIS EXISTS. #2574 argues that the load-bearing gap in identity-layer
detection is that it reads column NAMES only, and that wiring value signals in
would close it. Part A of the close plan drove the measurable error on both
scored corpora to zero -- and every failure it fixed was name-side. That leaves
the value-signal question live but unmeasured, because **neither scored corpus
can see the shape values would rescue**: the FK corpus renames every column with
a party qualifier by construction, so a column whose name carries no party never
occurs in it.

This is the instrument that decides whether Part C is worth building. It does
not detect anything new; it asks how much of the frontier is name-invisible.

FOUR BUCKETS, deliberately reported apart, because only one of them is a
question a value signal can answer.

* **ungrouped** -- the column landed in ``unassigned``. Detection found no party
  for it at all. **This is the only bucket a party-identifying value signal
  could open**, and it is the shape the issue's own ``routing number -> bank``
  example describes.
* **vocabulary gap** -- the column is in a real layer that carries its OWN
  qualifier (``film``, ``rental``, ``orderdetail``), and ``role`` reads
  "unknown" only because no pack declares that word. Nothing about the NAME is
  missing; extending a role vocabulary closes it. Counting these as name-blind
  is the mistake this audit was rewritten to stop making.
* **unnamed** -- grouped into a layer with no qualifier to name it by. This is
  the bucket that would need values to NAME an existing group.
* **no structure** -- the whole-frame fallback fired: no party qualifier
  anywhere, so detection honestly reports one population. Values would need to
  partition a frame from scratch, which is a far stronger claim than naming one.

Separately, **fused** layers -- two true parties predicted as one, measurable
only where ground truth exists (the FK corpus). For each, the audit asks the
question that decides who owns the fix: are the fused parties' REMAINDER
VOCABULARIES disjoint? If ``p_npi``/``p_specialty`` and
``p_firstname``/``p_birthdate`` share no remainder token, a smarter NAME-side
rule could split them and no value signal is required. Reporting that stops a
name-side defect being filed as evidence for values -- the exact mistake this
workstream keeps having to correct, and one this audit caught being made about
its own Part A residual.

A column counts as unnameable only if **no pack anywhere** declares a role hint
matching any of its tokens. A column named ``customer_ref`` that some pack could
have named is a vocabulary gap, not headroom for values.

    python scripts/layers_nameblind_audit.py
    python scripts/layers_nameblind_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "packages" / "python" / "infermap" / "tests" / "fixtures"
BLIND = FIXTURES / "layers_blind_labels.json"
PRECISION = FIXTURES / "layers_precision_corpus.json"

sys.path.insert(0, str(ROOT / "scripts"))


def _all_role_tokens() -> set[str]:
    """Every token any pack declares as a role hint, across all 16 verticals.

    The union rather than one pack's view: the question is whether a
    name-based approach could EVER name this column, not whether the pack that
    happened to be detected does.
    """
    from goldencheck_types import list_domains, load_domain
    from infermap.detect import _tokens

    out: set[str] = set()
    for name in list_domains():
        for role in load_domain(name).roles.values():
            for hint in role.name_hints:
                out.update(_tokens(hint))
            out.update(_tokens(role.name))
    return out


def _classify_frame(columns: list[str], role_tokens: set[str]) -> dict:
    """Per-frame ungrouped / unnamed / named counts."""
    from goldencheck_types import UNKNOWN_ROLE
    from infermap import detect_identity_layers
    from infermap.detect import _tokens

    result = detect_identity_layers(SimpleNamespace(columns=columns))

    unnamed_cols: list[str] = []
    vocab_gap_cols: list[str] = []
    no_structure_cols: list[str] = []
    named = 0
    for layer in result.layers:
        if layer.role != UNKNOWN_ROLE:
            named += len(layer.columns)
        elif layer.reason == "singleton" and len(layer.columns) == len(columns):
            # The whole-frame fallback: no party qualifier anywhere, so the
            # honest reading is one homogeneous population. Counting these as
            # "grouped but unnamed" would inflate that figure with frames where
            # nothing was grouped at all -- a different problem with a different
            # fix, and not one a party-identifying value signal addresses.
            no_structure_cols.extend(layer.columns)
        elif layer.evidence.get("qualifier"):
            # The layer HAS a name -- its own qualifier (`film`, `rental`). No
            # pack declares it, so `role` reads "unknown", but nothing about
            # the NAME is missing: this is a vocabulary gap, and a value signal
            # is not what closes it. What IS missing is `kind`, which no name
            # yields without a vocabulary. See the module docstring.
            vocab_gap_cols.extend(layer.columns)
        else:
            unnamed_cols.extend(layer.columns)

    def nameable(col: str) -> bool:
        return bool(set(_tokens(col)) & role_tokens)

    ungrouped_blind = [c for c in result.unassigned if not nameable(c)]
    unnamed_blind = [c for c in unnamed_cols if not nameable(c)]
    no_structure_blind = [c for c in no_structure_cols if not nameable(c)]
    return {
        "vocab_gap": len(vocab_gap_cols),
        "n_columns": len(columns),
        "named": named,
        "ungrouped": len(result.unassigned),
        "ungrouped_name_blind": len(ungrouped_blind),
        "unnamed": len(unnamed_cols),
        "unnamed_name_blind": len(unnamed_blind),
        "no_structure": len(no_structure_cols),
        "no_structure_name_blind": len(no_structure_blind),
        "examples": (ungrouped_blind + unnamed_blind)[:4],
    }


def _fusions(role_tokens: set[str]) -> list[dict]:
    """Layers that merged two or more TRUE parties, on the FK corpus.

    For each, whether the merged parties' remainder vocabularies are disjoint --
    the test of whether a name-side rule could still separate them.
    """
    from infermap import detect_identity_layers
    from infermap.detect import _tokens
    from layers_fk_groundtruth import SCHEMAS, denormalize

    spec = json.loads(Path(SCHEMAS).read_text(encoding="utf-8"))
    out: list[dict] = []
    for schema in spec["schemas"]:
        for conv_name, conv in spec["conventions"].items():
            if conv_name.startswith("_"):
                continue
            columns, truth = denormalize(schema, conv)
            result = detect_identity_layers(SimpleNamespace(columns=columns))
            for layer in result.layers:
                parties = sorted({truth[c] for c in layer.columns if c in truth})
                if len(parties) < 2:
                    continue
                # Remainder vocabulary per true party, within this fused layer.
                vocab: dict[str, set[str]] = {}
                for col in layer.columns:
                    party = truth.get(col)
                    if party is None:
                        continue
                    toks = _tokens(col)
                    # Drop the shared qualifier; what is left describes the field.
                    rest = set(toks) - {layer.evidence.get("qualifier", "")}
                    vocab.setdefault(party, set()).update(rest)
                shared: set[str] = set()
                seen: set[str] = set()
                for party, toks in vocab.items():
                    shared |= seen & toks
                    seen |= toks
                out.append(
                    {
                        "schema": schema["name"],
                        "convention": conv_name,
                        "qualifier": layer.evidence.get("qualifier", ""),
                        "merged_parties": parties,
                        "shared_remainder_tokens": sorted(shared),
                        "name_separable": not shared,
                    }
                )
    return out


def run() -> dict:
    role_tokens = _all_role_tokens()

    frames: list[dict] = []

    blind = json.loads(BLIND.read_text(encoding="utf-8"))
    for entry in blind["schemas"]:
        if entry.get("skip"):
            continue
        row = _classify_frame(entry["columns"], role_tokens)
        row["corpus"] = "blind_labels"
        row["id"] = entry["id"]
        frames.append(row)

    precision = json.loads(PRECISION.read_text(encoding="utf-8"))
    for group in ("negatives", "positives"):
        for entry in precision[group]:
            row = _classify_frame(entry["columns"], role_tokens)
            row["corpus"] = f"precision/{group}"
            row["id"] = entry["name"]
            frames.append(row)

    from layers_fk_groundtruth import SCHEMAS, denormalize

    spec = json.loads(Path(SCHEMAS).read_text(encoding="utf-8"))
    for schema in spec["schemas"]:
        for conv_name, conv in spec["conventions"].items():
            if conv_name.startswith("_"):
                continue
            columns, _truth = denormalize(schema, conv)
            row = _classify_frame(columns, role_tokens)
            row["corpus"] = f"fk/{conv_name}"
            row["id"] = schema["name"]
            frames.append(row)

    total_cols = sum(f["n_columns"] for f in frames)
    ungrouped_blind = sum(f["ungrouped_name_blind"] for f in frames)
    unnamed_blind = sum(f["unnamed_name_blind"] for f in frames)
    no_structure_blind = sum(f["no_structure_name_blind"] for f in frames)
    vocab_gap = sum(f["vocab_gap"] for f in frames)

    fusions = _fusions(role_tokens)
    return {
        "frames": frames,
        "fusions": fusions,
        "summary": {
            "n_frames": len(frames),
            "n_columns": total_cols,
            "ungrouped_name_blind": ungrouped_blind,
            "unnamed_name_blind": unnamed_blind,
            "ungrouped_name_blind_pct": ungrouped_blind / max(total_cols, 1),
            "unnamed_name_blind_pct": unnamed_blind / max(total_cols, 1),
            "no_structure_name_blind": no_structure_blind,
            "no_structure_name_blind_pct": no_structure_blind / max(total_cols, 1),
            "frames_no_structure": sum(1 for f in frames if f["no_structure"]),
            "vocab_gap": vocab_gap,
            "vocab_gap_pct": vocab_gap / max(total_cols, 1),
            "frames_with_any": sum(
                1 for f in frames if f["ungrouped_name_blind"] or f["unnamed_name_blind"]
            ),
            "n_fusions": len(fusions),
            "n_fusions_name_separable": sum(1 for f in fusions if f["name_separable"]),
        },
    }


def report(result: dict) -> None:
    s = result["summary"]
    print("=" * 78)
    print("NAME-BLIND SURFACE: what a value signal could rescue, and nothing else")
    print("=" * 78)
    print(f"  frames {s['n_frames']}   columns {s['n_columns']}")
    print()
    print(
        "  UNGROUPED and unnameable   "
        f"{s['ungrouped_name_blind']:4d}  ({s['ungrouped_name_blind_pct']:.1%})"
        "   no party found at all"
    )
    print(
        "  UNNAMED   and unnameable   "
        f"{s['unnamed_name_blind']:4d}  ({s['unnamed_name_blind_pct']:.1%})"
        "   grouped, but the party has no name"
    )
    print(
        "  VOCABULARY GAP             "
        f"{s['vocab_gap']:4d}  ({s['vocab_gap_pct']:.1%})"
        "   grouped AND self-named; no pack word"
    )
    print(
        "  NO STRUCTURE (whole-frame) "
        f"{s['no_structure_name_blind']:4d}  ({s['no_structure_name_blind_pct']:.1%})"
        "   no qualifier anywhere; one population"
    )
    print(
        f"  frames with at least one   {s['frames_with_any']:4d} of {s['n_frames']}"
        f"   (no-structure frames: {s['frames_no_structure']})"
    )
    print()
    print(f"  FUSED layers (>=2 true parties as one)  {s['n_fusions']}")
    print(
        f"    of which remainder vocabularies are DISJOINT "
        f"(a name-side rule could still split them): "
        f"{s['n_fusions_name_separable']}"
    )
    for f in result["fusions"]:
        mark = (
            "name-separable"
            if f["name_separable"]
            else "shares: " + ", ".join(f["shared_remainder_tokens"])
        )
        print(
            f"    {f['schema']:22s} {f['convention']:8s} "
            f"`{f['qualifier']}` {'+'.join(f['merged_parties']):28s} {mark}"
        )
    print("-" * 78)


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
