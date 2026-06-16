"""Threshold sweep for the embedding-ANN rows (`emb-ann` / `emb-st` / `emb-openai`).

Embeds every mention ONCE via a given provider, then scans cosine thresholds and
prints overall + per-class F1 so a *round, non-overfit* cut can be picked (the
overall-F1 peak is usually a plateau, not a spike). The committed `emb-*` rows
hard-code the chosen value; re-run this when the dataset or embedder changes.

    python erkgbench/sweep.py --provider local     # sentence-transformers MiniLM (no key)
    python erkgbench/sweep.py --provider openai     # text-embedding-3-small (needs OPENAI_API_KEY)
    python erkgbench/sweep.py --provider inhouse     # offline char-n-gram baseline
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import metrics  # noqa: E402
from erkgbench.adapters.base import Record, cluster_by_pairwise  # noqa: E402

DATASET = _BENCH_ROOT / "dataset" / "records.csv"


def _load() -> tuple[list[Record], list[str], list[str]]:
    records: list[Record] = []
    entity_ids: list[str] = []
    failure_classes: list[str] = []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            records.append(
                Record(
                    index=int(row["record_id"]),
                    mention=row["mention"],
                    entity_type=row["entity_type"],
                    context=row["context"],
                )
            )
            entity_ids.append(row["entity_id"])
            failure_classes.append(row["failure_class"])
    return records, entity_ids, failure_classes


def _embed(provider: str, texts: list[str]) -> np.ndarray:
    if provider == "inhouse":
        from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel

        vecs = np.asarray(GoldenEmbedModel().embed(texts), dtype=np.float32)
    else:
        from goldenmatch.embeddings.providers import resolve_provider

        vecs = np.asarray(resolve_provider(provider).embed(texts), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0.0, 1.0, norms)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, help="local | openai | inhouse | <name>")
    ap.add_argument("--lo", type=float, default=0.20)
    ap.add_argument("--hi", type=float, default=0.75)
    ap.add_argument("--step", type=float, default=0.025)
    args = ap.parse_args()

    records, entity_ids, failure_classes = _load()
    ordered = sorted(records, key=lambda r: r.index)
    vecs = _embed(args.provider, [r.mention for r in ordered])
    sim = vecs @ vecs.T

    off = sim[np.triu_indices_from(sim, k=1)]
    print(f"provider={args.provider}  n={len(ordered)}")
    print(f"cosine off-diagonal: min={off.min():.3f} med={np.median(off):.3f} max={off.max():.3f}")
    print(f"{'thr':>5} {'P':>6} {'R':>6} {'F1':>6} {'abbr':>6} {'synm':>6} {'coll*P':>7} {'temp*P':>7} {'nclus':>6}")

    thr = args.lo
    while thr <= args.hi + 1e-9:
        t = round(thr, 3)

        def pred(a: Record, b: Record, _t: float = t) -> bool:
            return bool(sim[a.index, b.index] >= _t)

        clustering = cluster_by_pairwise(ordered, pred)
        by_class = metrics.score_by_class(entity_ids, failure_classes, clustering)
        o = by_class["__overall__"]

        def f1(name: str) -> float:
            s = by_class.get(name)
            return s.f1 if s else 0.0

        def prec(name: str) -> float:
            s = by_class.get(name)
            return s.precision if s else 0.0

        nmulti = sum(1 for c in clustering if len(c) > 1)
        print(
            f"{t:>5} {o.precision:>6.3f} {o.recall:>6.3f} {o.f1:>6.3f} "
            f"{f1('abbreviation'):>6.3f} {f1('synonym_brand'):>6.3f} "
            f"{prec('same_name_collision'):>7.3f} {prec('temporal_version'):>7.3f} {nmulti:>6}"
        )
        thr += args.step


if __name__ == "__main__":
    main()
