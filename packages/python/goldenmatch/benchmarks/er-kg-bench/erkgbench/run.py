"""Run every adapter over the labelled dataset and emit the scoreboard.

    python -m erkgbench.run                 # from the er-kg-bench/ dir
    python erkgbench/run.py --embedder st   # activate cosine terms via MiniLM

Outputs ``results/results.json`` and ``results/RESULTS.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# Allow running as a loose script (python erkgbench/run.py) as well as -m.
_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import metrics  # noqa: E402
from erkgbench.adapters import (  # noqa: E402
    GoldenMatchAdapter,
    GoldenMatchEmbAnnAdapter,
    Record,
    all_modeled,
)
from erkgbench.adapters.base import last_cost_of  # noqa: E402
from erkgbench.adapters.real import available_real_adapters  # noqa: E402

DATASET = _BENCH_ROOT / "dataset" / "records.csv"
RESULTS_DIR = _BENCH_ROOT / "results"

CLASS_ORDER = [
    "abbreviation",
    "nickname_alias",
    "synonym_brand",
    "same_name_collision",
    "cross_lingual",
    "typo",
    "org_suffix",
    "temporal_version",
    "cross_document_exact",
]
# Classes that are NEGATIVE tests: distinct entities with colliding surface
# forms. The headline metric for these is PRECISION (avoid wrong merges).
PRECISION_CRITICAL = {"same_name_collision", "temporal_version"}


def load_records() -> tuple[list[Record], list[str], list[str]]:
    if not DATASET.exists():
        raise FileNotFoundError(
            f"{DATASET} not found. Build it from the real sources first:\n"
            "  python dataset/build_real.py   (curated QIDs/drugs in dataset/sources.jsonl)"
        )
    records: list[Record] = []
    entity_ids: list[str] = []
    failure_classes: list[str] = []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            i = int(row["record_id"])
            records.append(
                Record(
                    index=i,
                    mention=row["mention"],
                    entity_type=row["entity_type"],
                    context=row["context"],
                )
            )
            entity_ids.append(row["entity_id"])
            failure_classes.append(row["failure_class"])
    return records, entity_ids, failure_classes


def make_embedder(kind: str | None):
    if not kind:
        return None
    # all-MiniLM-L6-v2 is the model Neo4j's builder actually uses for its
    # cosine term, so it is the faithful choice when activating embeddings.
    from sentence_transformers import SentenceTransformer  # type: ignore

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def embed(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, normalize_embeddings=False).tolist()

    return embed


def run(embedder_kind: str | None) -> dict:
    records, entity_ids, failure_classes = load_records()
    embed_fn = make_embedder(embedder_kind)

    # Dogfood: goldenmatch runs zero-config (auto-config picks the strategy),
    # the same posture as every framework running at its documented default.
    adapters = [
        GoldenMatchAdapter(mode="auto"),
        GoldenMatchAdapter(mode="auto_fields"),
        # Embedding-ANN candidate generation via goldenmatch's offline (no-key,
        # no-torch) in-house embedder -- the lever the LLM experiment pointed at.
        GoldenMatchEmbAnnAdapter(),
    ]
    # The semantic-class attackers only activate with a key. Skip rather than
    # fake it; both stay out of the committed table (recorded as prose), since
    # they are not reproducible without a key.
    if os.environ.get("OPENAI_API_KEY"):
        # Embedding-ANN with a SEMANTIC embedder (world knowledge), the lever
        # the offline char-ngram path can't pull: it generates the candidate
        # pairs string blocking misses for abbreviation + synonym. Threshold
        # 0.55 from a small sweep (peak overall F1; stable 0.525-0.6 plateau).
        adapters.append(GoldenMatchEmbAnnAdapter(threshold=0.55, provider="openai"))
        adapters.append(GoldenMatchAdapter(mode="auto_llm"))
    # String-only rows stay canonical/unchanged (embed_fn=None) regardless of
    # --embedder, so the committed numbers don't silently shift identity. With an
    # embedder, the cosine-activated variants are ADDED alongside (Neo4j-KGBuilder(emb)
    # + LlamaIndex-PGI(emb)) so the board SHOWS the embedder's effect side-by-side.
    adapters += list(all_modeled(embed_fn=None))
    if embed_fn is not None:
        from erkgbench.adapters.modeled import emb_modeled  # noqa: E402

        adapters += emb_modeled(embed_fn)
    adapters += available_real_adapters()

    rows = []
    for ad in adapters:
        try:
            t0 = time.perf_counter()
            clustering = ad.resolve(records)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            # Cost of THIS run's resolve() (the timed one). Captured before the
            # determinism re-run so it reflects a single resolve. last_cost_of
            # returns zeros for any adapter that doesn't spend (most do not).
            cost = last_cost_of(ad)
            # Determinism: identical partition on a re-run.
            deterministic = metrics.clusterings_equal(clustering, ad.resolve(records))
        except Exception as exc:  # noqa: BLE001 - a flaky adapter must not sink the board
            rows.append({"name": ad.name, "defaults": ad.defaults, "fidelity": getattr(ad, "fidelity", "modeled"), "error": str(exc)[:200]})
            print(f"  [skip] {ad.name}: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        by_class = metrics.score_by_class(entity_ids, failure_classes, clustering)
        overall = by_class["__overall__"]
        rows.append(
            {
                "name": ad.name,
                "defaults": ad.defaults,
                "fidelity": getattr(ad, "fidelity", "modeled"),
                "overall": {
                    "precision": round(overall.precision, 3),
                    "recall": round(overall.recall, 3),
                    "f1": round(overall.f1, 3),
                },
                "per_class_f1": {
                    c: round(by_class[c].f1, 3) for c in CLASS_ORDER if c in by_class
                },
                "per_class_precision": {
                    c: round(by_class[c].precision, 3)
                    for c in CLASS_ORDER
                    if c in by_class
                },
                "time_ms": round(elapsed_ms, 1),
                "deterministic_floor": bool(deterministic and ad.deterministic),
                "cost": cost,
            }
        )

    return {
        "dataset": {
            "records": len(records),
            "entities": len(set(entity_ids)),
            "classes": CLASS_ORDER,
        },
        "embedder": embedder_kind or "none (string predicates only)",
        "precision_critical_classes": sorted(PRECISION_CRITICAL),
        "results": rows,
    }


def _short(c: str) -> str:
    return {
        "abbreviation": "abbr",
        "nickname_alias": "nick",
        "synonym_brand": "synm",
        "same_name_collision": "coll*",
        "cross_lingual": "xling",
        "typo": "typo",
        "org_suffix": "suffix",
        "temporal_version": "temp*",
        "cross_document_exact": "xdoc",
    }[c]


def _llm_flag(r: dict) -> str:
    """`yes` when the row's resolve() spent any LLM call, else `no`.

    Reads the raw ``cost`` dict the runner records (``last_cost_of``). A row
    missing ``cost`` (e.g. a legacy result) reads as ``no`` -- the same as a
    deterministic adapter, which is the safe default.
    """
    return "yes" if (r.get("cost") or {}).get("llm_calls", 0) > 0 else "no"


def _render_headline_table(results: list[dict]) -> list[str]:
    """Header + separator + one row per result for the headline F1 table.

    Factored out (pure function over the result rows) so the column-count
    invariant -- header / separator / every data row carry the same number of
    cells, including the ``LLM?`` column -- is unit-testable without running
    the full pipeline.
    """
    lines = [
        "| System | P | R | F1 | fid | coll&nbsp;P* | temp&nbsp;P* | ms | det-floor | LLM? |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['name']} | _error_ | | | {r.get('fidelity', '-')} | | | | "
                f"{r['error'][:40]} | {_llm_flag(r)} |"
            )
            continue
        o = r["overall"]
        cp = r["per_class_precision"].get("same_name_collision", "-")
        tp = r["per_class_precision"].get("temporal_version", "-")
        lines.append(
            f"| {r['name']} | {o['precision']} | {o['recall']} | **{o['f1']}** | "
            f"{r.get('fidelity', '-')} | {cp} | {tp} | {r['time_ms']} | "
            f"{'yes' if r['deterministic_floor'] else 'no'} | {_llm_flag(r)} |"
        )
    return lines


def to_markdown(report: dict) -> str:
    ds = report["dataset"]
    lines = [
        "# ER-KG-Bench results",
        "",
        f"Dataset: **{ds['records']} records / {ds['entities']} entities / "
        f"{len(ds['classes'])} failure classes**. Embedder: "
        f"`{report['embedder']}`.",
        "",
        "`*` = precision-critical negative class (distinct entities with "
        "colliding surface forms; lower precision = wrong merges).",
        "",
        "## Headline (pairwise, full set)",
        "",
    ]
    lines += _render_headline_table(report["results"])

    lines += ["", "## Per-class F1", "", "| System | " + " | ".join(
        _short(c) for c in CLASS_ORDER
    ) + " |", "|---|" + "---|" * len(CLASS_ORDER)]
    for r in report["results"]:
        if "error" in r:
            continue
        cells = " | ".join(
            str(r["per_class_f1"].get(c, "-")) for c in CLASS_ORDER
        )
        lines.append(f"| {r['name']} | {cells} |")

    lines += ["", "## Documented defaults (what each row runs)", ""]
    for r in report["results"]:
        lines.append(f"- **{r['name']}** — {r['defaults']}")
    lines += [
        "",
        "> Each row carries a `fid` tier (see `adapters/FIDELITY.md`): `real-inproc` "
        "runs the framework's real decision code; `validated` reproduces its exact "
        "rule confirmed vs source; `modeled` is an unconfirmed/divergent re-impl. "
        "mem0's LLM ADD/UPDATE merge layer stays out of scope (non-deterministic, "
        "per-pair LLM cost; Phase 3) -- this board runs each framework's "
        "deterministic dedup, including Graphiti's MinHash/Jaccard floor.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--embedder",
        choices=["st"],
        default=None,
        help="Activate cosine OR-terms via sentence-transformers all-MiniLM-L6-v2.",
    )
    args = ap.parse_args()

    report = run("st" if args.embedder == "st" else None)
    RESULTS_DIR.mkdir(exist_ok=True)
    # Explicit utf-8: the markdown carries em dashes / accented exonyms, and
    # Path.write_text defaults to the locale encoding (cp1252 on Windows), which
    # would mojibake them and diverge from a Linux/CI regeneration.
    (RESULTS_DIR / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (RESULTS_DIR / "RESULTS.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
