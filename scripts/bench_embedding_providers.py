#!/usr/bin/env python3
"""#506 Step 3: pipeline-level embedding-provider comparison.

Runs the FULL ER pipeline (dedupe / match) with the embedding scorer backed by
each provider and reports F1 / precision / recall, so we can answer the #506
acceptance question: is the in-house embedder within ~1-2% of Vertex on
structured data?

Arms (`--providers`, default `none,inhouse`):
  none     no embedding scorer (lexical auto-config baseline).
  inhouse  a `goldenmatch.embeddings.inhouse` model trained in-process on the
           dataset's ground-truth pairs (Step 2), backing the embedding scorer.
  vertex   Google Vertex AI embeddings. Requires GOLDENMATCH_GPU_MODE=vertex +
           ADC / google-cloud-aiplatform creds (pull from Infisical on the
           bench box). Skipped with a notice if creds/mode are absent.

Datasets (`--datasets`, default `febrl3`):
  febrl3   recordlinkage synthetic ER (dedupe). No download.
  dblp-acm Leipzig bibliographic linkage (match). Needs --datasets-dir.

The lexical base config comes from `auto_configure_df` (the shipped zero-config),
so each arm differs ONLY by the embedding scorer. `rerank=False` is forced on
every weighted matchkey to avoid the offline cross-encoder HF download.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make `dqbench_adapters.*` and the sibling bench script importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl

import goldenmatch
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.embedder import _embedders

EMBED_SCORERS = ("embedding", "record_embedding")


def _force_no_rerank(cfg: GoldenMatchConfig) -> None:
    for mk in cfg.get_matchkeys():
        if mk.type == "weighted":
            mk.rerank = False


def _apply_provider(
    base: GoldenMatchConfig, provider: str, inhouse_path: str, embed_columns: list[str]
) -> GoldenMatchConfig:
    """Return a deep copy of `base` with the embedding scorer set to `provider`.

    none    -> strip any embedding fields (lexical only).
    inhouse -> point embedding fields at `inhouse:<path>`; add one over
               `embed_columns` if the base has none.
    vertex  -> same, model name is a placeholder (Vertex is selected by
               GOLDENMATCH_GPU_MODE=vertex inside get_embedder).
    """
    cfg = base.model_copy(deep=True)
    mks = cfg.get_matchkeys()
    has_emb = any(f.scorer in EMBED_SCORERS for mk in mks for f in mk.fields)

    if provider == "none":
        for mk in mks:
            mk.fields = [f for f in mk.fields if f.scorer not in EMBED_SCORERS]
        _force_no_rerank(cfg)
        return cfg

    model = f"inhouse:{inhouse_path}" if provider == "inhouse" else "text-embedding-004"
    if has_emb:
        for mk in mks:
            for f in mk.fields:
                if f.scorer in EMBED_SCORERS:
                    f.model = model
    else:
        wmk = next((mk for mk in mks if mk.type == "weighted"), None)
        if wmk is not None and embed_columns:
            wmk.fields.append(
                MatchkeyField(scorer="record_embedding", columns=embed_columns,
                              weight=1.0, model=model)
            )
    _force_no_rerank(cfg)
    return cfg


def _prf(found: set, gt: set) -> tuple[float, float, float, int, int, int]:
    tp = len(found & gt); fp = len(found - gt); fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return f1, p, r, tp, fp, fn


# ── dataset runners ─────────────────────────────────────────────────────────

def _train_inhouse(pairs, dim: int, epochs: int, out: Path) -> str:
    from goldenmatch.embeddings.inhouse import FeaturizerConfig, TrainConfig, train_embedder
    model, _ = train_embedder(
        pairs,
        TrainConfig(dim=dim, epochs=epochs, lr=0.5, seed=0,
                    featurizer=FeaturizerConfig(n_features=4096)),
    )
    model.save(out)
    return str(out)


def run_febrl3(providers, inhouse_path, dim, epochs, tmp: Path):
    from dqbench_adapters.febrl3 import load_febrl3_df_and_gt
    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        print("febrl3: recordlinkage not installed — skipping"); return {}
    df, gt = loaded
    embed_cols = [c for c in ("given_name", "surname", "address_1", "suburb") if c in df.columns]

    if "inhouse" in providers:
        from bench_inhouse_embedder import _febrl3_pairs
        inhouse_path = _train_inhouse(_febrl3_pairs(seed=0), dim, epochs, tmp / "febrl3_model")

    base = goldenmatch.auto_configure_df(df)
    row_to_id = df["id"].to_list()
    out = {}
    for prov in providers:
        _embedders.clear()
        cfg = _apply_provider(base, prov, inhouse_path, embed_cols)
        t0 = time.time()
        res = goldenmatch.dedupe_df(df, config=cfg)
        found = set()
        for c in res.clusters.values():
            m = sorted(c["members"])
            for i in range(len(m)):
                for j in range(i + 1, len(m)):
                    a, b = row_to_id[m[i]], row_to_id[m[j]]
                    found.add((min(a, b), max(a, b)))
        out[prov] = (*_prf(found, gt), round(time.time() - t0, 1))
    return out


def run_dblp_acm(providers, inhouse_path, dim, epochs, datasets_dir: Path, tmp: Path):
    from dqbench_adapters.leipzig_eval import load_ground_truth
    d = datasets_dir / "DBLP-ACM"
    if not (d / "DBLP2.csv").exists():
        print("dblp-acm: dataset files missing — skipping"); return {}
    dblp = pl.read_csv(d / "DBLP2.csv", encoding="utf8-lossy", ignore_errors=True)
    acm = pl.read_csv(d / "ACM.csv", encoding="utf8-lossy", ignore_errors=True)
    gt = load_ground_truth(d / "DBLP-ACM_perfectMapping.csv", "idDBLP", "idACM")
    embed_cols = [c for c in ("title", "authors", "venue") if c in dblp.columns]

    if "inhouse" in providers:
        from bench_inhouse_embedder import _leipzig_pairs
        # _leipzig_pairs reads the CSVs directly from its dir arg, so pass the
        # DBLP-ACM dir itself (not its parent).
        inhouse_path = _train_inhouse(_leipzig_pairs("dblp-acm", str(d)),
                                      dim, epochs, tmp / "dblp_model")

    # Explicit bibliographic match config. auto_configure_df on the concatenated
    # frame produces a dedupe-shaped RED config that match_df can't use (F1
    # collapses ~0.5), so build a sensible base here: block on a title prefix to
    # tame the cross-source product, score title+authors lexically. The
    # embedding arms ADD a record_embedding over [title, authors] on top (via
    # _apply_provider), so the embedding scorer is the ONLY varied component.
    base = GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["title"], transforms=["lowercase", "strip", "substring:0:6"]),
                BlockingKeyConfig(fields=["title"], transforms=["lowercase", "strip", "substring:4:10"]),
            ],
        ),
        matchkeys=[MatchkeyConfig(
            name="biblio", type="weighted", threshold=0.6, rerank=False,
            fields=[
                MatchkeyField(field="title", scorer="token_sort", weight=1.0, transforms=["lowercase", "strip"]),
                MatchkeyField(field="authors", scorer="token_sort", weight=0.6, transforms=["lowercase", "strip"]),
            ],
        )],
    )
    dblp_ids = dblp["id"].cast(pl.Utf8).to_list()
    acm_ids = acm["id"].cast(pl.Utf8).to_list()
    n_dblp = len(dblp_ids)
    out = {}
    for prov in providers:
        _embedders.clear()
        cfg = _apply_provider(base, prov, inhouse_path, embed_cols)
        t0 = time.time()
        res = goldenmatch.match_df(dblp, acm, config=cfg)
        found = set()
        matched = getattr(res, "matched", None)
        if matched is not None and matched.height > 0:
            for row in matched.iter_rows(named=True):
                tgt, ref = row["__target_row_id__"], row["__ref_row_id__"]
                d_idx, a_idx = (tgt, ref - n_dblp) if tgt < n_dblp else (ref, tgt - n_dblp)
                if 0 <= d_idx < n_dblp and 0 <= a_idx < len(acm_ids):
                    found.add((str(dblp_ids[d_idx]), str(acm_ids[a_idx])))
        out[prov] = (*_prf(found, gt), round(time.time() - t0, 1))
    return out


def run_synthetic(providers, dim, epochs, tmp: Path, n_rows: int, seed: int = 42):
    """Scalable synthetic person dedupe (the 'larger' beefy-box case): n_rows
    base records, 30% duplicated with field corruption; GT = (orig, dup) pairs.
    Lets the Railway job stress the pipeline at a size the dev box can't."""
    import random
    rng = random.Random(seed)
    # Generate large, near-unique name pools from syllable combos. A small
    # fixed vocab (~20 names) is catastrophic at scale: thousands of DISTINCT
    # people collide on the same name and the pipeline merges them all
    # (fp explosion, F1 -> 0.15). 3-syllable combos give ~13k distinct tokens,
    # so distinct entities rarely collide and only planted dups truly match.
    SYL = ["an","be","ca","da","el","fi","ga","ha","in","jo","ka","la",
           "ma","na","or","pa","ri","sa","ta","va","wo","xe","yu","ze"]
    def _name():
        return rng.choice(SYL) + rng.choice(SYL) + rng.choice(SYL)
    ST = ["main st","oak ave","pine rd","maple dr","cedar ln","elm st","washington ave","park blvd"]
    CT = ["springfield","franklin","clinton","georgetown","salem","fairview","madison","bristol"]
    rows = [{"id": f"r{i}", "first_name": _name(), "last_name": _name(),
             "address": f"{rng.randint(1,9999)} {rng.choice(ST)}", "city": rng.choice(CT),
             "zip": f"{rng.randint(10000,99999)}", "birth_year": str(rng.randint(1940,2005))}
            for i in range(n_rows)]

    def corrupt(s):
        if len(s) < 3:
            return s
        p = rng.randint(0, len(s) - 1)
        return s[:p] + rng.choice("abcdefghijklmnopqrstuvwxyz") + s[p + 1:]

    gt, dups = set(), []
    for idx in rng.sample(range(n_rows), n_rows * 3 // 10):
        o = rows[idx]; c = dict(o); c["id"] = o["id"] + "_DUP"
        for f in ("first_name", "last_name", "address"):
            if rng.random() < 0.4:
                c[f] = corrupt(c[f])
        dups.append(c); gt.add((o["id"], c["id"]))
    df = pl.DataFrame(rows + dups)
    embed_cols = ["first_name", "last_name", "address", "city"]

    inhouse_path = ""
    if "inhouse" in providers:
        def txt(r): return " ".join(str(r[k]) for k in ("first_name", "last_name", "address", "city"))
        by = {r["id"]: r for r in rows + dups}
        pairs = []
        for a, b in gt:
            pairs.append((txt(by[a]), txt(by[b]), 1))
            pairs.append((txt(by[a]), txt(rows[rng.randint(0, n_rows - 1)]), 0))
        inhouse_path = _train_inhouse(pairs, dim, epochs, tmp / "synth_model")

    base = goldenmatch.auto_configure_df(df)
    row_to_id = df["id"].to_list()
    out = {}
    for prov in providers:
        _embedders.clear()
        cfg = _apply_provider(base, prov, inhouse_path, embed_cols)
        t0 = time.time()
        res = goldenmatch.dedupe_df(df, config=cfg)
        found = set()
        for c in res.clusters.values():
            m = sorted(c["members"])
            for i in range(len(m)):
                for j in range(i + 1, len(m)):
                    a, b = row_to_id[m[i]], row_to_id[m[j]]
                    found.add((min(a, b), max(a, b)))
        out[prov] = (*_prf(found, gt), round(time.time() - t0, 1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets", default="febrl3")
    ap.add_argument("--providers", default="none,inhouse")
    ap.add_argument("--datasets-dir", type=Path,
                    default=Path("packages/python/goldenmatch/tests/benchmarks/datasets"))
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--synthetic-rows", type=int, default=50000,
                    help="row count for the 'synthetic' (larger) dataset")
    ap.add_argument("--summary-md", type=Path, default=None)
    args = ap.parse_args(argv)
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    if "vertex" in providers and os.environ.get("GOLDENMATCH_GPU_MODE") != "vertex":
        print("vertex requested but GOLDENMATCH_GPU_MODE!=vertex — dropping vertex arm "
              "(set GOLDENMATCH_GPU_MODE=vertex + Vertex creds to include it)")
        providers = [p for p in providers if p != "vertex"]

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="emb_prov_"))
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    lines = ["## Embedding-provider pipeline comparison (#506 Step 3)", ""]
    for ds in datasets:
        if ds == "febrl3":
            res = run_febrl3(providers, "", args.dim, args.epochs, tmp)
        elif ds == "dblp-acm":
            res = run_dblp_acm(providers, "", args.dim, args.epochs, args.datasets_dir, tmp)
        elif ds == "synthetic":
            res = run_synthetic(providers, args.dim, args.epochs, tmp, args.synthetic_rows)
        else:
            print(f"unknown dataset {ds}"); continue
        if not res:
            continue
        print(f"\n=== {ds} ===")
        lines += [f"### {ds}", "", "| provider | F1 | precision | recall | tp | fp | fn | wall |",
                  "|---|---|---|---|---|---|---|---|"]
        for prov, (f1, p, r, tp, fp, fn, wall) in res.items():
            print(f"  {prov:8s} F1={f1:.4f} P={p:.4f} R={r:.4f} (tp={tp} fp={fp} fn={fn}) {wall}s")
            lines.append(f"| {prov} | {f1:.4f} | {p:.4f} | {r:.4f} | {tp} | {fp} | {fn} | {wall}s |")
        lines.append("")
    if args.summary_md:
        args.summary_md.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
