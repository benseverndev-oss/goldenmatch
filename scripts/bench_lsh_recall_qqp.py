#!/usr/bin/env python
"""Real-corpus MinHash/LSH recall on Quora Question Pairs (#1081).

QQP is a labeled near-duplicate text benchmark (pairs of questions tagged
duplicate / not-duplicate). This measures how well the LSH blocker recovers the
labeled duplicate pairs (recall) while cutting comparison work (reduction), on
real text rather than synthetic edits.

Acquisition is pinned to HuggingFace ``datasets`` (``load_dataset("quora")``);
the full corpus is downloaded only inside the bench job, NEVER committed (Quora
licensing). A tiny SYNTHETIC stand-in (``tests/fixtures/qqp_sample.csv``, shaped
like QQP but not real Quora rows) drives a CI smoke test.

``measure_qqp_recall`` works on a list of ``(q1, q2, is_duplicate)`` rows so the
smoke test can feed the sample without any download.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from goldenmatch.config.schemas import LSHKeyConfig, SimHashKeyConfig  # noqa: E402
from goldenmatch.core.lsh_blocker import MinHashLSHBlocker  # noqa: E402
from goldenmatch.core.simhash_blocker import SimHashLSHBlocker  # noqa: E402


def _build_truth(
    pairs: list[tuple[str, str, bool]],
) -> tuple[list[str], set[tuple[int, int]], dict[tuple[int, int], bool]]:
    """Deduplicate questions and build the (questions, truth, labeled) accounting.

    Shared by both the lexical and semantic methods so recall/reduction/precision
    are computed identically — only the candidate-pair generation differs.
    ``truth`` = labeled DUPLICATE pairs; ``labeled`` = every pair carrying a label
    (a subset of all pairs, since QQP labels only some pairs).
    """
    index: dict[str, int] = {}

    def gid(q: str) -> int:
        if q not in index:
            index[q] = len(index)
        return index[q]

    truth: set[tuple[int, int]] = set()
    labeled: dict[tuple[int, int], bool] = {}
    for q1, q2, dup in pairs:
        a, b = gid(q1), gid(q2)
        if a == b:
            continue  # identical text -> not an informative pair
        key = (a, b) if a < b else (b, a)
        labeled[key] = dup
        if dup:
            truth.add(key)

    questions: list[str] = [""] * len(index)
    for q, i in index.items():
        questions[i] = q
    return questions, truth, labeled


def _score_candidates(
    questions: list[str],
    truth: set[tuple[int, int]],
    labeled: dict[tuple[int, int], bool],
    candidates: set[tuple[int, int]],
    num_bands: int,
) -> dict:
    """Recall / reduction / precision-on-labeled for a candidate-pair set."""
    n = len(questions)
    all_pairs = n * (n - 1) // 2
    found = sum(1 for p in truth if p in candidates)
    cand_labeled = [p for p in candidates if p in labeled]
    precision = (
        sum(1 for p in cand_labeled if labeled[p]) / len(cand_labeled) if cand_labeled else None
    )
    return {
        "num_questions": n,
        "num_bands": num_bands,
        "labeled_duplicate_pairs": len(truth),
        "candidate_pairs": len(candidates),
        "recall": found / len(truth) if truth else 1.0,
        "reduction": 1.0 - (len(candidates) / all_pairs) if all_pairs else 1.0,
        "precision_on_labeled": precision,
    }


def measure_qqp_recall(
    pairs: list[tuple[str, str, bool]],
    mode: str = "word",
    k: int = 2,
    num_perms: int = 128,
    threshold: float = 0.5,
    seed: int = 0,
) -> dict:
    """LEXICAL (MinHash/LSH shingle) recall of labeled duplicate pairs.

    ``pairs`` is ``(question1, question2, is_duplicate)``. Recall is over the
    labeled DUPLICATE pairs; precision-on-labeled is the duplicate fraction among
    candidate pairs that carry a label (an approximation — QQP labels only a
    subset of all pairs).
    """
    questions, truth, labeled = _build_truth(pairs)
    blocker = MinHashLSHBlocker.from_config(
        LSHKeyConfig(column="q", mode=mode, k=k, num_perms=num_perms, threshold=threshold, seed=seed)
    )
    candidates = blocker.candidate_pairs(questions)
    return _score_candidates(questions, truth, labeled, candidates, blocker.num_bands)


def measure_qqp_recall_semantic(
    pairs: list[tuple[str, str, bool]],
    num_planes: int = 256,
    num_bands: int = 32,
    seed: int = 0,
    embed_model: str = "all-MiniLM-L6-v2",
) -> dict:
    """SEMANTIC (embedding + SimHash/LSH) recall of labeled duplicate pairs.

    Embeds the unique questions, then SimHash-projects + bands the embeddings so
    cosine-near questions collide. Same recall/reduction/precision-on-labeled
    accounting as the lexical method (only candidate generation differs).
    """
    import numpy as np
    from goldenmatch.core.embedder import get_embedder

    questions, truth, labeled = _build_truth(pairs)
    emb = get_embedder(embed_model).embed_column(questions, cache_key="qqp")
    emb = np.asarray(emb, dtype=np.float64)
    blocker = SimHashLSHBlocker.from_config(
        SimHashKeyConfig(column="q", num_planes=num_planes, num_bands=num_bands, seed=seed)
    )
    candidates = blocker.candidate_pairs(emb)
    return _score_candidates(questions, truth, labeled, candidates, blocker.num_bands)


def load_sample(path: Path) -> list[tuple[str, str, bool]]:
    """Load a QQP-shaped CSV (columns: q1, q2, is_duplicate)."""
    rows: list[tuple[str, str, bool]] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((r["q1"], r["q2"], str(r["is_duplicate"]).strip() in ("1", "true", "True")))
    return rows


def load_qqp_hf(
    max_rows: int | None = None, dataset: str = "SetFit/qqp"
) -> list[tuple[str, str, bool]]:
    """Download a labeled QQP dataset via HuggingFace datasets (bench job only).

    Defaults to ``SetFit/qqp`` — a PARQUET-native QQP (no loading script, no
    external CDN), so it works on ``datasets`` 3.x (the old ``quora`` dataset
    carries a ``quora.py`` script that 3.x refuses). Column mapping is flexible:
    SetFit's ``text1``/``text2``/``label`` and GLUE's ``question1``/``question2``/
    ``label`` are both handled. Rows with a hidden test label (-1) are skipped.
    Override ``dataset`` (the ``--dataset`` flag / workflow input) to swap source.
    """
    from datasets import load_dataset  # imported lazily; only the job installs it

    ds = load_dataset(dataset, split="train")
    cols = set(ds.column_names)
    if {"text1", "text2"} <= cols:
        c1, c2 = "text1", "text2"
    elif {"question1", "question2"} <= cols:
        c1, c2 = "question1", "question2"
    else:
        raise ValueError(f"unrecognized QQP schema for {dataset!r}: {sorted(cols)}")

    rows: list[tuple[str, str, bool]] = []
    for ex in ds:
        label = ex["label"]
        if label not in (0, 1):
            continue  # hidden test label
        rows.append((str(ex[c1]), str(ex[c2]), label == 1))
        if max_rows is not None and len(rows) >= max_rows:
            break
    return rows


def _format_report(m: dict, cfg: dict, method: str, dataset: str, rows_per_band: int) -> str:
    prec = "n/a" if m["precision_on_labeled"] is None else f"{m['precision_on_labeled']:.4f}"
    method_label = "embedding + SimHash/LSH" if method == "semantic" else "MinHash/LSH (lexical)"
    return (
        f"# {method_label} recall on Quora Question Pairs (#1081/#1082)\n\n"
        f"- method: `{method}`\n"
        f"- dataset: `{dataset}`\n"
        f"- config: `{cfg}`\n"
        f"- unique questions: {m['num_questions']:,}\n"
        f"- bands x rows: {m['num_bands']} x {rows_per_band}\n"
        f"- labeled duplicate pairs: {m['labeled_duplicate_pairs']:,}\n"
        f"- candidate pairs: {m['candidate_pairs']:,}\n\n"
        f"| metric | value |\n|---|---|\n"
        f"| recall | {m['recall']:.4f} |\n"
        f"| reduction | {m['reduction']:.4f} |\n"
        f"| precision (on labeled pairs) | {prec} |\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--method",
        choices=("lexical", "semantic"),
        default="lexical",
        help="lexical = MinHash/LSH shingles; semantic = embedding + SimHash/LSH",
    )
    ap.add_argument("--mode", default="word", help="(lexical) shingle mode")
    ap.add_argument("--k", type=int, default=2, help="(lexical) shingle size")
    ap.add_argument("--num-perms", type=int, default=128, help="(lexical) MinHash permutations")
    ap.add_argument("--threshold", type=float, default=0.5, help="(lexical) Jaccard threshold")
    ap.add_argument("--num-planes", type=int, default=256, help="(semantic) SimHash hyperplanes")
    ap.add_argument("--num-bands", type=int, default=32, help="(semantic) SimHash LSH bands")
    ap.add_argument(
        "--embed-model", default="all-MiniLM-L6-v2", help="(semantic) embedding model"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-rows", type=int, default=None, help="cap QQP rows (smoke runs)")
    ap.add_argument("--sample", type=Path, default=None, help="use a QQP-shaped CSV instead of HF")
    ap.add_argument("--dataset", default="SetFit/qqp", help="HuggingFace QQP dataset id")
    ap.add_argument("--out", type=Path, default=Path("lsh_qqp_report.md"))
    args = ap.parse_args()

    pairs = load_sample(args.sample) if args.sample else load_qqp_hf(args.max_rows, args.dataset)
    dataset = "sample-csv" if args.sample else args.dataset

    if args.method == "semantic":
        cfg = {
            "num_planes": args.num_planes,
            "num_bands": args.num_bands,
            "embed_model": args.embed_model,
            "seed": args.seed,
        }
        m = measure_qqp_recall_semantic(
            pairs,
            num_planes=args.num_planes,
            num_bands=args.num_bands,
            seed=args.seed,
            embed_model=args.embed_model,
        )
        rows_per_band = args.num_planes // m["num_bands"]
    else:
        cfg = {
            "mode": args.mode,
            "k": args.k,
            "num_perms": args.num_perms,
            "threshold": args.threshold,
            "seed": args.seed,
        }
        m = measure_qqp_recall(pairs, **cfg)
        rows_per_band = args.num_perms // m["num_bands"]

    report = _format_report(m, cfg, args.method, dataset, rows_per_band)
    args.out.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
