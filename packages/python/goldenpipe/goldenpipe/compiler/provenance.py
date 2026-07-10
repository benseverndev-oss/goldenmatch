"""Pure field-level provenance over the SP1 IR — the mirror of
goldenpipe-core/src/provenance.rs. provenance(CompiledPipeline) -> {fields, unmapped}."""
from __future__ import annotations


def provenance(compiled: dict) -> dict:
    nodes = compiled.get("nodes", [])
    fields: dict[str, dict] = {}
    order: list[str] = []
    unmapped: list[dict] = []
    blocking: set[str] = set()
    scorer: set[str] = set()

    def field(col: str) -> dict:
        if col not in fields:
            fields[col] = {
                "column": col, "origin": "source", "checks": [], "transforms": [],
                "blocking_key": False, "scorer_input": False, "node_ids": [],
            }
            order.append(col)
        return fields[col]

    for n in nodes:  # nodes already in id order from lower()
        kind = n.get("kind")
        if kind == "Scan":
            f = field(n["column"])
            f["checks"].extend(n.get("ops", []))
            f["node_ids"].append(n["id"])
        elif kind == "Map":
            f = field(n["column"])
            f["transforms"].append(n["op"])
            f["node_ids"].append(n["id"])
        elif kind == "Partition":
            for k in (n.get("keys") or []):
                blocking.add(k)
        elif kind == "PairScore":
            for c in ((n.get("scorer") or {}).get("columns") or []):
                scorer.add(c)
        else:  # Source / Connected / Barrier
            unmapped.append({"node_id": n["id"], "kind": kind, "note": _note(kind)})

    # role-only columns (no Scan/Map) appended in SORTED order — set iteration is
    # nondeterministic and would flake golden-vector / Rust-parity.
    for col in sorted(blocking | scorer):
        field(col)
    for col, f in fields.items():
        f["blocking_key"] = col in blocking
        f["scorer_input"] = col in scorer

    return {"fields": [fields[c] for c in order], "unmapped": unmapped}


def _note(kind: str) -> str:
    return {"Source": "data loaded", "Connected": "clustering", "Barrier": "opaque stage"}.get(kind, kind)
