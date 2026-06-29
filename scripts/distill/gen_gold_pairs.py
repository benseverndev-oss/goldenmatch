"""Stage 1 (key-free): generate gold (text -> schema-canonical triple) training pairs.

No teacher LLM, no API. The engineered corpus ENCODES ground truth (each edge is
src::rel::dst with a canonical direction), so we synthesize perfect supervision for
free. Per edge we emit several PHRASINGS -- forward AND reverse/passive -- all mapped
to the SAME canonical-direction triple. The reverse-phrasing augmentation is the
lesson: teach the 7B to emit `(subj, rel, obj)` in canonical direction no matter how
the sentence is phrased, which is the residual (non-passive direction swaps) that the
ingest-time canonicalization structurally cannot fix.

Train on a DIFFERENT seed than the eval corpus so the eval stays a genuine held-out
test of learned extraction, not memorized edges.

Usage:
    python scripts/distill/gen_gold_pairs.py --seed 7 --n-entities 120 \
        --out scripts/distill/data/pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Light imports only -- the engineered loader is pure (no polars/goldenmatch).
_BENCH = Path(__file__).resolve().parents[2] / "packages/python/goldenmatch/benchmarks/er-kg-bench"
if str(_BENCH) not in sys.path:
    sys.path.insert(0, str(_BENCH))

from erkgbench.qa_e2e.engineered import RELATION_SCHEMA, _load_entities  # noqa: E402

#: Reverse/passive surface phrasings per relation. A sentence using one of these states
#: the edge BACKWARDS ("B was acquired by A" = A acquired B), so the gold triple keeps
#: canonical direction while the text is reversed -- the exact pattern that teaches the
#: model to canonicalize direction. Mirrors goldengraph.schema reverse aliases.
_REVERSE_PHRASING: dict[str, tuple[str, ...]] = {
    "works_at": ("employs", "is the employer of"),
    "located_in": ("contains", "is home to"),
    "acquired": ("was acquired by", "was bought by"),
    "authored": ("was authored by", "was written by"),
    "part_of": ("contains", "includes"),
}
_FORWARD_PHRASING: dict[str, tuple[str, ...]] = {
    "works_at": ("works at", "is employed at"),
    "located_in": ("located in", "is based in"),
    "acquired": ("acquired", "bought"),
    "authored": ("authored", "wrote"),
    "part_of": ("part of", "is part of"),
}


def _build_edges(entities, seed: int):
    """Same edge-graph construction as engineered.generate_engineered: each entity gets
    2-4 distinct relations, at most one edge per (entity, relation)."""
    rng = random.Random(seed)
    ids = [e.id for e in entities]
    by_id = {e.id: e for e in entities}
    edges = []
    for e in entities:
        n = rng.randint(2, 4)
        rels = rng.sample(RELATION_SCHEMA, min(n, len(RELATION_SCHEMA)))
        for rel in rels:
            dst = rng.choice(ids)
            if dst != e.id:
                edges.append((by_id[e.id], rel, by_id[dst]))
    return edges, rng


def _record(subj_name: str, obj_name: str, rel: str, text: str) -> dict:
    """A training pair: text -> entities + one canonical-direction relationship (subj=0, obj=1)."""
    return {
        "text": text,
        "entities": [
            {"name": subj_name, "type": "concept", "context": ""},
            {"name": obj_name, "type": "concept", "context": ""},
        ],
        "relationships": [{"subj": 0, "predicate": rel, "obj": 1}],
        "attributes": [],
    }


def gen_pairs(seed: int, n_entities: int, reverse_frac: float):
    """Yield gold training records. For each edge: one forward-phrased record, and with
    probability `reverse_frac` one reverse-phrased record (entities swapped in the TEXT,
    triple kept canonical). Entity order in `entities` always matches the canonical triple
    (subj first), so the student learns direction from the schema, not text position."""
    entities = _load_entities()
    if n_entities:
        entities = entities[:n_entities]
    edges, rng = _build_edges(entities, seed)
    for src, rel, dst in edges:
        s, o = src.canonical, dst.canonical
        fwd = rng.choice(_FORWARD_PHRASING[rel])
        yield _record(s, o, rel, f"{s} {fwd} {o}.")
        if rng.random() < reverse_frac:
            rev = rng.choice(_REVERSE_PHRASING[rel])
            # reverse phrasing: object stated first, subject second -> SAME canonical triple
            yield _record(s, o, rel, f"{o} {rev} {s}.")


#: Eval corpus seed (run_qa_e2e generate_engineered) -- training MUST exclude it so the
#: eval stays a genuine held-out test.
_EVAL_SEED = 20260620


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="7,8,9,11,13,17,19,23",
                    help="comma-separated edge-graph seeds (MUST exclude the eval seed)")
    ap.add_argument("--n-entities", type=int, default=0, help="0 = all entities")
    ap.add_argument("--reverse-frac", type=float, default=0.6,
                    help="fraction of edges that also get a reverse-phrased example")
    ap.add_argument("--out", default="scripts/distill/data/pairs.jsonl")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if _EVAL_SEED in seeds:
        raise SystemExit(f"refusing to train on the eval seed {_EVAL_SEED}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    n, n_rev = 0, 0
    with out.open("w", encoding="utf-8") as f:
        for seed in seeds:
            for rec in gen_pairs(seed, args.n_entities, args.reverse_frac):
                text = rec["text"]
                if text in seen:
                    continue
                seen.add(text)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                if text.split()[0] != rec["entities"][0]["name"].split()[0]:
                    n_rev += 1
    print(f"wrote {n} unique pairs ({n_rev} reverse-phrased) from {len(seeds)} seeds -> {out}",
          flush=True)


if __name__ == "__main__":
    main()
