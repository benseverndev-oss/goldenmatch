"""Collective-ER headline benchmark.

Runs three author-resolution configurations on a synthetic co-authorship
fixture across multiple seeds, then writes a pairwise P/R/F1 scoreboard
to ``benchmarks/collective-er/results/RESULTS.md`` and
``benchmarks/collective-er/results/results.json``.

Run command (from repo root or from the package dir):
    $py="D:\\show_case\\goldenmatch\\.venv\\Scripts\\python.exe"
    $env:PYTHONPATH="D:\\show_case\\gm-collective\\packages\\python\\goldenmatch"
    $env:GOLDENMATCH_NATIVE="0"; $env:POLARS_SKIP_CPU_CHECK="1"; $env:PYTHONIOENCODING="utf-8"
    cd D:\\show_case\\gm-collective\\packages\\python\\goldenmatch
    & $py benchmarks/collective-er/run.py

Environment requirements (set before running):
    GOLDENMATCH_NATIVE=0        -- disables native Rust kernel (stale-wheel safe)
    POLARS_SKIP_CPU_CHECK=1     -- prevents Polars WMI hang on Windows
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path: make goldenmatch and tests.collective_er importable
# ---------------------------------------------------------------------------
_PACKAGE_ROOT = Path(__file__).parent.parent.parent  # packages/python/goldenmatch
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

# ---------------------------------------------------------------------------
# Runtime environment guard (belt-and-suspenders)
# ---------------------------------------------------------------------------
os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

# ---------------------------------------------------------------------------
# Imports (after path is set up)
# ---------------------------------------------------------------------------
import polars as pl  # noqa: E402
from tests.collective_er.fixture import generate_relational_fixture  # noqa: E402
from tests.collective_er.metrics import pairwise_prf  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEEDS: list[int] = [7, 8, 9]
N_ENTITIES: int = 40   # matches the unit-test fixture size; runs in ~10s/seed

# Collective tuning point (from task-8 calibration in test_collective_er.py)
_COLLECTIVE_ALPHA = 0.65
_COLLECTIVE_REL_THRESHOLD = 0.50

# ---------------------------------------------------------------------------
# Shared author config (mirrors _author_config() in test_collective_er.py)
# ---------------------------------------------------------------------------

def _author_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    mk = MatchkeyConfig(
        name="name_fuzzy",
        type="weighted",
        threshold=0.80,
        rerank=False,
        fields=[
            MatchkeyField(
                field="name",
                scorer="jaro_winkler",
                weight=1.0,
                transforms=["lowercase", "strip"],
            )
        ],
    )
    blocking = BlockingConfig(
        keys=[
            BlockingKeyConfig(
                fields=["name"],
                transforms=["soundex"],
            )
        ],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


# ---------------------------------------------------------------------------
# Three scorer helpers (logic reused from test_collective_er.py)
# ---------------------------------------------------------------------------

def _independent_author_f1(fixture, tmp_path: Path):
    """Attribute-only ER on author names."""
    import goldenmatch

    cfg = _author_config()
    authors_df = fixture.authors.select(["__row_id__", "name"])
    result = goldenmatch.dedupe_df(authors_df, config=cfg)

    pred: dict = {}
    for cid, cinfo in result.clusters.items():
        for mid in cinfo["members"]:
            pred[mid] = cid
    all_ids = authors_df["__row_id__"].to_list()
    next_singleton = max(pred.values(), default=-1) + 1
    for rid in all_ids:
        if rid not in pred:
            pred[rid] = next_singleton
            next_singleton += 1

    return pairwise_prf(pred, fixture.truth)


def _flatboost_author_f1(fixture, tmp_path: Path):
    """Graph ER with additive evidence propagation (naive flat-boost baseline)."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.graph_er import EntityType, Relationship, run_graph_er

    authors_for_csv = fixture.authors.select(["__row_id__", "name"]).with_columns(
        pl.col("__row_id__").alias("author_row_id")
    )
    author_csv = str(tmp_path / "authors.csv")
    authors_for_csv.write_csv(author_csv)

    authorship_w_pid = (
        fixture.authorship.join(
            fixture.papers.rename({"__row_id__": "paper_row_id"}),
            on="paper_row_id",
            how="left",
        )
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    paper_csv = str(tmp_path / "paper_authorship.csv")
    authorship_w_pid.write_csv(paper_csv)

    author_cfg = _author_config()
    paper_mk = MatchkeyConfig(
        name="paper_exact",
        type="exact",
        fields=[MatchkeyField(field="paper_id", transforms=["strip"])],
    )
    paper_blocking = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["paper_id"], transforms=[])],
    )
    paper_cfg = GoldenMatchConfig(matchkeys=[paper_mk], blocking=paper_blocking)

    author_entity = EntityType(name="author", sources=[(author_csv, "authors")], config=author_cfg)
    paper_entity = EntityType(name="paper", sources=[(paper_csv, "paper_authorship")], config=paper_cfg)
    rel = Relationship(
        from_entity="paper", to_entity="author",
        join_key="author_row_id", evidence_weight=0.4,
    )
    result = run_graph_er(
        entities=[author_entity, paper_entity],
        relationships=[rel],
        max_iterations=3,
        propagation_mode="additive",
    )

    author_et = result.entities["author"]
    pred: dict = {}
    for cid, cinfo in author_et.clusters.items():
        for mid in cinfo["members"]:
            pred[mid] = cid
    all_ids = fixture.authors["__row_id__"].to_list()
    next_singleton = max(pred.values(), default=-1) + 1
    for rid in all_ids:
        if rid not in pred:
            pred[rid] = next_singleton
            next_singleton += 1

    return pairwise_prf(pred, fixture.truth)


def _collective_author_f1(fixture, tmp_path: Path):
    """Graph ER with relational (collective) propagation at tuned alpha=0.65, threshold=0.50."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.graph_er import EntityType, Relationship, run_graph_er

    authors_for_csv = fixture.authors.select(["__row_id__", "name"]).with_columns(
        pl.col("__row_id__").alias("author_row_id")
    )
    author_csv = str(tmp_path / "authors.csv")
    authors_for_csv.write_csv(author_csv)

    authorship_w_pid = (
        fixture.authorship.join(
            fixture.papers.rename({"__row_id__": "paper_row_id"}),
            on="paper_row_id",
            how="left",
        )
        .with_row_index("__row_id__")
        .with_columns(pl.col("__row_id__").cast(pl.Int64))
    )
    paper_csv = str(tmp_path / "paper_authorship.csv")
    authorship_w_pid.write_csv(paper_csv)

    author_cfg = _author_config()
    paper_mk = MatchkeyConfig(
        name="paper_exact",
        type="exact",
        fields=[MatchkeyField(field="paper_id", transforms=["strip"])],
    )
    paper_blocking = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["paper_id"], transforms=[])],
    )
    paper_cfg = GoldenMatchConfig(matchkeys=[paper_mk], blocking=paper_blocking)

    author_entity = EntityType(name="author", sources=[(author_csv, "authors")], config=author_cfg)
    paper_entity = EntityType(name="paper", sources=[(paper_csv, "paper_authorship")], config=paper_cfg)
    rel = Relationship(
        from_entity="paper", to_entity="author",
        join_key="author_row_id", evidence_weight=0.4,
    )
    result = run_graph_er(
        entities=[author_entity, paper_entity],
        relationships=[rel],
        max_iterations=10,
        propagation_mode="relational",
        alpha=_COLLECTIVE_ALPHA,
        rel_threshold=_COLLECTIVE_REL_THRESHOLD,
    )

    author_et = result.entities["author"]
    pred: dict = {}
    for cid, cinfo in author_et.clusters.items():
        for mid in cinfo["members"]:
            pred[mid] = cid
    all_ids = fixture.authors["__row_id__"].to_list()
    next_singleton = max(pred.values(), default=-1) + 1
    for rid in all_ids:
        if rid not in pred:
            pred[rid] = next_singleton
            next_singleton += 1

    return pairwise_prf(pred, fixture.truth)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Collective-ER benchmark  n_entities={N_ENTITIES}  seeds={SEEDS}")
    print(f"  configs: independent | flat-boost | collective (alpha={_COLLECTIVE_ALPHA}, thr={_COLLECTIVE_REL_THRESHOLD})")
    print()

    per_seed: list[dict] = []
    t_total_start = time.perf_counter()

    for seed in SEEDS:
        t_seed = time.perf_counter()
        print(f"  seed={seed} ...", end=" ", flush=True)
        fx = generate_relational_fixture(seed=seed, n_entities=N_ENTITIES)

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "indep").mkdir()
            (tmp / "flat").mkdir()
            (tmp / "coll").mkdir()

            t0 = time.perf_counter()
            p_i, r_i, f_i = _independent_author_f1(fx, tmp / "indep")
            dt_i = time.perf_counter() - t0

            t0 = time.perf_counter()
            p_f, r_f, f_f = _flatboost_author_f1(fx, tmp / "flat")
            dt_f = time.perf_counter() - t0

            t0 = time.perf_counter()
            p_c, r_c, f_c = _collective_author_f1(fx, tmp / "coll")
            dt_c = time.perf_counter() - t0

        dt_seed = time.perf_counter() - t_seed
        print(
            f"indep F1={f_i:.3f}  flat F1={f_f:.3f}  coll F1={f_c:.3f}"
            f"  lift={f_c - f_i:+.3f}  ({dt_seed:.1f}s)"
        )

        per_seed.append({
            "seed": seed,
            "n_entities": N_ENTITIES,
            "independent": {"P": p_i, "R": r_i, "F1": f_i, "wall_s": dt_i},
            "flat_boost":  {"P": p_f, "R": r_f, "F1": f_f, "wall_s": dt_f},
            "collective":  {"P": p_c, "R": r_c, "F1": f_c, "wall_s": dt_c},
        })

    total_wall = time.perf_counter() - t_total_start

    # Averages
    def _avg(key, metric):
        return sum(row[key][metric] for row in per_seed) / len(per_seed)

    avg = {
        "seeds": SEEDS,
        "n_entities": N_ENTITIES,
        "total_wall_s": round(total_wall, 1),
        "independent": {m: _avg("independent", m) for m in ("P", "R", "F1")},
        "flat_boost":  {m: _avg("flat_boost",  m) for m in ("P", "R", "F1")},
        "collective":  {m: _avg("collective",  m) for m in ("P", "R", "F1")},
    }

    # ---------------------------------------------------------------------------
    # Markdown table
    # ---------------------------------------------------------------------------
    lines = [
        "# Collective-ER Headline Benchmark",
        "",
        f"**n_entities**: {N_ENTITIES}  |  **seeds**: {SEEDS}  |  **total wall**: {total_wall:.1f}s",
        "",
        f"Collective config: `run_graph_er(propagation_mode=\"relational\", alpha={_COLLECTIVE_ALPHA}, rel_threshold={_COLLECTIVE_REL_THRESHOLD})`",
        "",
        "## Per-seed results",
        "",
        "| seed | config      |    P  |    R  |   F1  | lift vs indep |",
        "|-----:|:------------|------:|------:|------:|--------------:|",
    ]
    for row in per_seed:
        s = row["seed"]
        fi = row["independent"]["F1"]
        ff = row["flat_boost"]["F1"]
        fc = row["collective"]["F1"]
        lines.append(f"| {s}    | independent | {row['independent']['P']:.3f} | {row['independent']['R']:.3f} | {fi:.3f} |               |")
        lines.append(f"| {s}    | flat-boost  | {row['flat_boost']['P']:.3f} | {row['flat_boost']['R']:.3f} | {ff:.3f} | {ff - fi:+.3f}       |")
        lines.append(f"| {s}    | collective  | {row['collective']['P']:.3f} | {row['collective']['R']:.3f} | {fc:.3f} | {fc - fi:+.3f}       |")

    lines += [
        "",
        "## Averages across seeds",
        "",
        "| config      |    P  |    R  |   F1  |",
        "|:------------|------:|------:|------:|",
        f"| independent | {avg['independent']['P']:.3f} | {avg['independent']['R']:.3f} | {avg['independent']['F1']:.3f} |",
        f"| flat-boost  | {avg['flat_boost']['P']:.3f} | {avg['flat_boost']['R']:.3f} | {avg['flat_boost']['F1']:.3f} |",
        f"| collective  | {avg['collective']['P']:.3f} | {avg['collective']['R']:.3f} | {avg['collective']['F1']:.3f} |",
        "",
        "## Takeaway",
        "",
        f"Collective ER (relational propagation) averages **F1={avg['collective']['F1']:.3f}** vs "
        f"independent F1={avg['independent']['F1']:.3f} and flat-boost F1={avg['flat_boost']['F1']:.3f}. "
        f"Lift over attribute-only baseline: **{avg['collective']['F1'] - avg['independent']['F1']:+.3f}**.",
    ]

    md = "\n".join(lines) + "\n"

    # Print to stdout
    print()
    print(md)

    # ---------------------------------------------------------------------------
    # Write outputs
    # ---------------------------------------------------------------------------
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "RESULTS.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"Wrote {md_path}")

    json_payload = {"avg": avg, "per_seed": per_seed}
    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
