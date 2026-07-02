"""WhoIsWho SND runner: fetch -> per-name dedupe/graph_er -> Pairwise-F1.

Engines (``--engine``):
    relational     dedupe_df with the co-author-OR-orgtext config (HEADLINE)
    coauthor_only  dedupe_df, co-author Jaccard alone (relational ablation)
    text_only      dedupe_df, topical similarity only (unresolved straw baseline)
    zero_config    dedupe_df(df) unassisted (what goldenmatch picks alone)
    graph_er       run_graph_er relational/collective propagation (decision #3)
    all_singletons trivial floor: every paper its own author
    all_one        trivial floor: all a name's papers = one author

Usage (from the package dir, with the package importable):
    python benchmarks/whoiswho-snd/run_snd.py --split valid --engine relational
    python benchmarks/whoiswho-snd/run_snd.py --split valid --engine relational --limit 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# make `goldenmatch` and the sibling harness modules importable
_HERE = Path(__file__).parent
_PKG_ROOT = _HERE.parent.parent  # packages/python/goldenmatch
for p in (str(_HERE), str(_PKG_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# stale-native-wheel + polars-cpu-check guards, matching collective-er
os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import fetch  # noqa: E402
import scorers  # noqa: E402
from score import ground_truth_clusters, pairwise_f1_macro  # noqa: E402
from to_frame import PAPER_ID_COL, build_name_frame, clusters_to_pid_lists  # noqa: E402


def _name_to_pids(split_data: dict) -> dict[str, list[str]]:
    """The papers-per-name map to cluster (valid: raw; train: keys of GT)."""
    if "raw" in split_data:
        return {k: list(v) for k, v in split_data["raw"].items()}
    gt = split_data["ground_truth"]
    return {name: [p for pids in aid_map.values() for p in pids]
            for name, aid_map in gt.items()}


def _predict_dedupe(df, config):
    import goldenmatch as gm

    result = gm.dedupe_df(df, config=config, confidence_required=False)
    return clusters_to_pid_lists(result.clusters, df)


def _predict_graph_er(name, df, tmp_dir):
    """Collective (relational) graph-ER over the paper<->co-author graph.

    Papers are the entity to cluster; co-author membership is the relationship
    carrying the propagated evidence. Modeled on collective-er/run.py.
    """
    import polars as pl
    from configs import _const_blocking
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.graph_er import EntityType, Relationship, run_graph_er

    # paper entity: attribute config = org+text+venue (co-author handled relationally)
    paper_cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="orgtext", type="weighted", threshold=0.55,
                                  rerank=False, fields=[
                                      MatchkeyField(field="orgs", scorer="set_jaccard", weight=2.0),
                                      MatchkeyField(field="text", scorer="token_sort", weight=1.0),
                                  ])],
        blocking=_const_blocking(),
    )
    paper_csv = str(tmp_dir / "papers.csv")
    df.write_csv(paper_csv)

    # co-author entity: one row per (paper, coauthor); join_key back to paper row
    edge_rows = []
    for rid, coauthors in zip(df["__row_id__"].to_list(), df["coauthors"].to_list()):
        for ca in (coauthors or "").split("|"):
            if ca:
                edge_rows.append({"paper_row_id": rid, "coauthor": ca})
    if not edge_rows:
        return [[pid] for pid in df[PAPER_ID_COL].to_list()]
    edges = pl.DataFrame(edge_rows).with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64)
    )
    edge_csv = str(tmp_dir / "coauthor_edges.csv")
    edges.write_csv(edge_csv)

    coauthor_cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="ca_exact", type="exact",
                                  fields=[MatchkeyField(field="coauthor", transforms=["strip"])])],
        blocking=_const_blocking_for("coauthor"),
    )

    paper_entity = EntityType(name="paper", sources=[(paper_csv, "papers")], config=paper_cfg)
    coauthor_entity = EntityType(name="coauthor", sources=[(edge_csv, "coauthor_edges")],
                                 config=coauthor_cfg)
    rel = Relationship(from_entity="coauthor", to_entity="paper",
                       join_key="paper_row_id", evidence_weight=0.5)
    # SND is relation-PRIMARY (co-author overlap is the whole signal, not a boost
    # on top of attributes), so alpha is high and rel_threshold low -- unlike the
    # collective-er defaults (0.65/0.50) tuned for attribute-primary shapes.
    result = run_graph_er(
        entities=[paper_entity, coauthor_entity], relationships=[rel],
        max_iterations=10, propagation_mode="relational",
        alpha=float(os.environ.get("SND_GRAPHER_ALPHA", "0.9")),
        rel_threshold=float(os.environ.get("SND_GRAPHER_REL_THRESHOLD", "0.15")),
    )
    return clusters_to_pid_lists(result.entities["paper"].clusters, df)


def _const_blocking_for(field_name: str):
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    return BlockingConfig(keys=[BlockingKeyConfig(fields=[field_name], transforms=[])])


def run(split: str, engine: str, *, limit: int | None = None, data_dir=None,
        coauthor_threshold: float = 0.15, orgtext_threshold: float = 0.55) -> dict:
    import tempfile

    from configs import (
        coauthor_only_config,
        relational_config,
        text_only_config,
    )

    scorers.register()
    print(f"loading split={split} ...", file=sys.stderr)
    data = fetch.load_split(split, data_dir=data_dir)
    name_to_pids = _name_to_pids(data)
    truth = ground_truth_clusters(data["ground_truth"])
    pub = data["pub"]

    names = list(truth.keys())
    if limit:
        names = names[:limit]

    predictions: dict[str, list[list[str]]] = {}
    t0 = time.perf_counter()
    for i, name in enumerate(names, 1):
        pids = name_to_pids.get(name, [])
        df = build_name_frame(name, pids, pub)
        if df.height == 0:
            predictions[name] = []
            continue

        if engine == "all_singletons":
            predictions[name] = [[p] for p in df[PAPER_ID_COL].to_list()]
        elif engine == "all_one":
            predictions[name] = [df[PAPER_ID_COL].to_list()]
        elif engine == "graph_er":
            with tempfile.TemporaryDirectory() as td:
                predictions[name] = _predict_graph_er(name, df, Path(td))
        elif engine == "zero_config":
            import goldenmatch as gm
            res = gm.dedupe_df(df.drop("__block__"), confidence_required=False)
            predictions[name] = clusters_to_pid_lists(res.clusters, df)
        else:
            cfg = {
                "relational": lambda: relational_config(
                    coauthor_threshold=coauthor_threshold, orgtext_threshold=orgtext_threshold),
                "coauthor_only": lambda: coauthor_only_config(
                    coauthor_threshold=coauthor_threshold),
                "text_only": lambda: text_only_config(),
            }[engine]()
            predictions[name] = _predict_dedupe(df, cfg)

        print(f"  [{i}/{len(names)}] {name:22s} papers={df.height:5d} "
              f"pred_clusters={len(predictions[name]):4d}", file=sys.stderr)

    wall = time.perf_counter() - t0
    scored = pairwise_f1_macro(predictions, {n: truth[n] for n in names})
    scored["engine"] = engine
    scored["split"] = split
    scored["wall_s"] = round(wall, 1)
    return scored


def main():
    ap = argparse.ArgumentParser(description="WhoIsWho SND runner")
    ap.add_argument("--split", default="valid", choices=["valid", "train"])
    ap.add_argument("--engine", default="relational", choices=[
        "relational", "coauthor_only", "text_only", "zero_config", "graph_er",
        "all_singletons", "all_one"])
    ap.add_argument("--limit", type=int, default=None, help="score only the first N names")
    ap.add_argument("--coauthor-threshold", type=float, default=0.15)
    ap.add_argument("--orgtext-threshold", type=float, default=0.55)
    ap.add_argument("--out", default=None, help="write results json here")
    args = ap.parse_args()

    res = run(args.split, args.engine, limit=args.limit,
              coauthor_threshold=args.coauthor_threshold,
              orgtext_threshold=args.orgtext_threshold)

    print(f"\n== SND {args.engine} on {args.split} "
          f"(n_names={res['n_names']}) ==")
    print(f"  Pairwise-F1 (macro): {res['pairwise_f1_macro']:.4f}")
    print(f"  Precision   (macro): {res['pairwise_precision_macro']:.4f}")
    print(f"  Recall      (macro): {res['pairwise_recall_macro']:.4f}")
    print(f"  Pairwise-F1 (micro): {res['pairwise_f1_micro']:.4f}")
    print(f"  wall: {res['wall_s']}s")

    if args.out:
        slim = {k: v for k, v in res.items() if k != "per_name"}
        Path(args.out).write_text(json.dumps(slim, indent=2))
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
