"""Recall-lever probe: does semantic-embedding candidate generation crack the
recall-bound ER-KG-Bench classes (abbreviation / synonym / cross_lingual) that
string blocking + char-ngram embeddings miss?

For each embedder tier: embed the corpus mentions, sweep the cosine threshold,
cluster (union-find over pairs >= thr), score per-class. Reports the best-overall
threshold + the recall-bound classes specifically. This is a MEASUREMENT (not a
committed board row) -- it quantifies the lever before we wire it into the pipeline.

    python scripts/embed_recall_probe.py st st-multi      # free downloadable models
    python scripts/embed_recall_probe.py inhouse openai   # local: no-knowledge vs paid

Tiers:
  inhouse  goldenmatch char-ngram embedder (no world knowledge) -- today's emb-ann. Local.
  openai   goldenmatch resolve_provider("openai") semantic embedder. Needs OPENAI_API_KEY.
  st       sentence-transformers all-MiniLM-L6-v2 (free, English). Needs torch -> CI.
  st-multi sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 (free, multilingual).
"""
from __future__ import annotations

import csv
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("erkg_metrics", BENCH / "erkgbench" / "metrics.py")
assert _spec is not None and _spec.loader is not None
metrics = importlib.util.module_from_spec(_spec)
sys.modules["erkg_metrics"] = metrics
_spec.loader.exec_module(metrics)

records, eids, classes = [], [], []
with open(BENCH / "dataset" / "records.csv", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        records.append((int(row["record_id"]), row["mention"], row["entity_type"]))
        eids.append(row["entity_id"])
        classes.append(row["failure_class"])
MENTIONS = [m for _, m, _ in records]
N = len(records)

_CLASS_ORDER = [
    "abbreviation", "synonym_brand", "cross_lingual", "nickname_alias",
    "org_suffix", "typo", "temporal_version", "same_name_collision",
    "cross_document_exact",
]
_RECALL_BOUND = ("abbreviation", "synonym_brand", "cross_lingual")
_ST_MODELS = {
    "st": "sentence-transformers/all-MiniLM-L6-v2",
    "st-multi": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}


def embed(tier: str) -> np.ndarray:
    if tier == "inhouse":
        from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel
        return np.asarray(GoldenEmbedModel().embed(MENTIONS), dtype=np.float32)
    if tier == "openai":
        from goldenmatch.embeddings.providers import resolve_provider
        return np.asarray(resolve_provider("openai").embed(MENTIONS), dtype=np.float32)
    if tier in _ST_MODELS:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(_ST_MODELS[tier])
        return np.asarray(model.encode(MENTIONS, normalize_embeddings=False), dtype=np.float32)
    raise SystemExit(f"unknown tier {tier!r}")


def clusters_at(sim: np.ndarray, thr: float) -> list[list[int]]:
    parent = list(range(N))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(N):
        row = sim[i]
        for j in range(i + 1, N):
            if row[j] >= thr:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(N):
        groups.setdefault(find(i), []).append(i)
    return [sorted(v) for v in groups.values()]


def sweep(tier: str) -> None:
    print(f"\n=== {tier} ===", flush=True)
    vecs = embed(tier)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.where(norms == 0.0, 1.0, norms)
    sim = vecs @ vecs.T
    best_f1, best_thr, best_p, best_r = -1.0, 0.0, 0.0, 0.0
    best_perclass: dict[str, float] = {}
    for thr in [round(float(x), 2) for x in np.arange(0.30, 0.96, 0.05)]:
        clustering = clusters_at(sim, thr)
        bc = metrics.score_by_class(eids, classes, clustering)
        o = bc["__overall__"]
        rb = float(np.mean([bc[c].f1 for c in _RECALL_BOUND if c in bc]))
        print(f"  thr={thr:.2f}  P={o.precision:.3f} R={o.recall:.3f} F1={o.f1:.3f}  "
              f"recall-bound-F1={rb:.3f}", flush=True)
        if o.f1 > best_f1:
            best_f1, best_thr, best_p, best_r = o.f1, thr, o.precision, o.recall
            best_perclass = {
                str(c): float(bc[c].f1) for c in bc if c != "__overall__"
            }
    print(f"  -> BEST overall thr={best_thr:.2f}  P={best_p:.3f} "
          f"R={best_r:.3f} F1={best_f1:.3f}", flush=True)
    for c in _CLASS_ORDER:
        if c in best_perclass:
            mark = "  <- recall-bound" if c in _RECALL_BOUND else ""
            print(f"       {c:20s} {best_perclass[c]:.3f}{mark}", flush=True)


def main() -> None:
    tiers = sys.argv[1:] or ["inhouse"]
    print(f"corpus: {N} records / {len(set(eids))} entities", flush=True)
    for tier in tiers:
        if tier == "openai" and not os.environ.get("OPENAI_API_KEY"):
            print(f"\n[skip {tier}: no OPENAI_API_KEY]", flush=True)
            continue
        sweep(tier)


if __name__ == "__main__":
    main()
