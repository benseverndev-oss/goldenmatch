"""Seeded ground-truth near-dup injector for the corpus-dedup bench.

Real corpora ship no near-dup labels, so we synthesize them: deterministically layer a
controlled fraction of near-dups onto a base corpus and emit the corpus + a truth file.
Every output doc carries a `cluster_id` (truth) — an injected dup shares its source's
cluster; every base doc that is not duplicated is its own singleton. That truth is what
the evaluator scores recall against.

Determinism is the contract: a fixed (base_docs, seed, frac, mode_weights, strength) yields
the same corpus + truth (values + order), which is what makes the per-PR gate reproducible.

CLI:
  python inject_dups.py --corpus offline --n-docs 1500 --seed 0 --frac 0.4 --out-dir DIR
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import polars as pl

MODES = ("exact", "partial", "paraphrase")
_INSERT_CLAUSE = " note this inserted near duplicate marker clause "


def _exact(text: str, rng: random.Random) -> str:
    return text


def _partial(text: str, rng: random.Random) -> str:
    """Drop a contiguous run of tokens and insert a clause -> high but <1.0 overlap."""
    toks = text.split()
    if len(toks) < 8:
        return text + _INSERT_CLAUSE
    drop = max(1, int(len(toks) * 0.2))
    start = rng.randint(0, len(toks) - drop)
    kept = toks[:start] + toks[start + drop:]
    ins_at = rng.randint(0, len(kept))
    kept = kept[:ins_at] + _INSERT_CLAUSE.split() + kept[ins_at:]
    return " ".join(kept)


def _paraphrase(text: str, rng: random.Random, strength: float) -> str:
    """Case flips + a few adjacent-token swaps, bounded so it stays a near-dup."""
    toks = text.split()
    n_edits = max(1, int(len(toks) * strength))
    for _ in range(n_edits):
        if len(toks) < 2:
            break
        i = rng.randint(0, len(toks) - 2)
        if rng.random() < 0.5:
            toks[i], toks[i + 1] = toks[i + 1], toks[i]  # swap adjacent
        else:
            toks[i] = toks[i].upper() if toks[i].islower() else toks[i].lower()
    return " ".join(toks)


def _derive(mode: str, text: str, rng: random.Random, strength: float) -> str:
    if mode == "exact":
        return _exact(text, rng)
    if mode == "partial":
        return _partial(text, rng)
    return _paraphrase(text, rng, strength)


def build(
    base_docs: list[tuple[str, str]],
    *,
    seed: int,
    frac: float,
    out_dir: Path,
    mode_weights: tuple[float, float, float] = (0.34, 0.33, 0.33),
    strength: float = 0.15,
) -> tuple[Path, Path]:
    """Inject near-dups; write corpus.parquet + truth.parquet; return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    # corpus rows + truth rows, base docs first (each its own cluster).
    doc_ids = [did for did, _ in base_docs]
    texts = [t for _, t in base_docs]
    cluster_ids = list(doc_ids)  # base doc cluster == its own id

    # pick the sources to duplicate, deterministically.
    order = list(range(len(base_docs)))
    rng.shuffle(order)
    n_sources = round(frac * len(base_docs))
    sources = order[:n_sources]

    for k, idx in enumerate(sources):
        src_id, src_text = base_docs[idx]
        mode = rng.choices(MODES, weights=mode_weights, k=1)[0]
        dup_text = _derive(mode, src_text, rng, strength)
        doc_ids.append(f"{src_id}~dup-{mode}-{k}")
        texts.append(dup_text)
        cluster_ids.append(src_id)  # inherit the source's cluster

    corpus = pl.DataFrame({"doc_id": doc_ids, "text": texts})
    truth = pl.DataFrame({"record_id": doc_ids, "cluster_id": cluster_ids})

    corpus_path = out_dir / "corpus.parquet"
    truth_path = out_dir / "truth.parquet"
    corpus.write_parquet(corpus_path, compression="zstd")
    truth.write_parquet(truth_path, compression="zstd")
    return corpus_path, truth_path


def main() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import corpora

    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--n-docs", type=int, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frac", type=float, default=0.4)
    ap.add_argument("--strength", type=float, default=0.15)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    base = list(corpora.load_corpus(args.corpus, n_docs=args.n_docs, seed=args.seed))
    corpus_path, truth_path = build(
        base, seed=args.seed, frac=args.frac, out_dir=args.out_dir, strength=args.strength
    )
    print(f"[inject_dups] base={len(base)} -> corpus={corpus_path} truth={truth_path}")


if __name__ == "__main__":
    main()
