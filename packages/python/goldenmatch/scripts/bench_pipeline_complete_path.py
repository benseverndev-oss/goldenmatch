"""SP-C complete-path verdict bench (BINDING Phase-2 payoff measurement).

Measures the COMPLETE-PATH (A+B+C) end-to-end peak RSS + wall of the
cluster -> golden -> identity stage, legacy (frames-out OFF, dict) vs columnar
(frames-out ON, dict-free stage), at scale. This is the binding measurement
that decides whether Phase-2's columnar cutover lands the RSS win.

Two variants, each in its OWN subprocess (clean peak RSS):

  * ``columnar`` (GOLDENMATCH_CLUSTER_FRAMES_OUT=1): build pairs ->
    ``build_cluster_frames`` -> ``build_golden_records_from_frames`` -> identity
    cluster-rep prep (``ClusterPairScores.from_frames`` + per-cluster member iter
    via ``assignments.group_by``). No per-cluster ``dict[int, dict]`` is
    materialized in the stage.

  * ``legacy`` (no gate): build pairs -> ``build_clusters`` (dict) ->
    ``build_golden_records_batch`` -> identity cluster-rep prep
    (``ClusterPairScores.from_pairs`` + ``clusters.items()`` iter). The dict is
    live for the whole stage.

CONFOUND CONTROL (from the SP-C spec). The ``id_prep`` segment measures ONLY the
identity CLUSTER-REPRESENTATION work SP-C changed (the view + the per-cluster
member iteration the resolver loop drives). The full ``resolve_clusters`` +
``IdentityStore`` resolution is DELIBERATELY EXCLUDED: its store I/O is a shared,
frames-invariant confound that DOMINATES wall (a near-quadratic SQLite upsert
path -- it ran ~20 min at 1M before this reshape) and would MASK the
cluster-representation delta. Identity byte-identical parity is already proven by
the SP-C gates; the verdict axis here is the peak RSS over build + golden +
id_prep (where the dict-vs-frames representation delta lives), plus the 100M
feasibility (does columnar complete where the legacy dict OOMs).

At 100M the ``legacy`` variant is EXPECTED to OOM. The legacy child's non-zero
exit / OOM is CAUGHT and recorded as ``"legacy OOM"`` in the table rather than
crashing the whole bench, so the columnar feasibility point still reports.

Parity is NOT re-proven here (SP-A/B/C parity gates already proved
byte-identical). A cheap membership sanity check (columnar vs legacy cluster
count) runs once before the perf loop.

Default ``--np 1000000,5000000``; the workflow dispatch passes ``25000000`` (the
RSS-delta leg) and ``100000000`` (the feasibility point) explicitly.

Local smoke: python ... --np 50000 --runs 1 (resource is unavailable on Windows
so RSS is 0.0 -- fine). DO NOT run the real bench locally (box hangs on import).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _make_pairs_df(n_pairs_target: int):
    """M clusters of size k=5 -> 10 pairs each. Deterministic, no RNG.

    Mirrors the SP-A/SP-B benches so the cluster shape (many small clusters, no
    oversized) is identical -- this exercises the bulk-RSS axis, which is where
    the dict-vs-frames cluster representation delta lives.
    """
    import polars as pl

    k = 5
    per = k * (k - 1) // 2  # 10
    m = max(1, n_pairs_target // per)
    a_col: list[int] = []
    b_col: list[int] = []
    for c in range(m):
        base = c * k
        for i in range(k):
            for j in range(i + 1, k):
                a_col.append(base + i)
                b_col.append(base + j)
    s_col = [0.95] * len(a_col)
    return pl.DataFrame(
        {"id_a": a_col, "id_b": b_col, "score": s_col},
        schema={"id_a": pl.Int64, "id_b": pl.Int64, "score": pl.Float64},
    )


def _pairs_list_from_df(pairs_df) -> list[tuple[int, int, float]]:
    return list(
        zip(
            pairs_df["id_a"].to_list(),
            pairs_df["id_b"].to_list(),
            pairs_df["score"].to_list(),
        )
    )


def _make_source_df(n_members: int):
    """Source frame for golden + identity: one row per member id (0..n-1) with a
    ``__row_id__`` aligned to the pair member ids, ``__source__``, and a couple of
    payload columns so survivorship + the identity record hash have content."""
    import polars as pl

    ids = list(range(n_members))
    return pl.DataFrame(
        {
            "__row_id__": ids,
            "__source__": ["bench"] * n_members,
            "name": [f"rec{i}" for i in ids],
            "email": [f"rec{i}@example.com" for i in ids],
        },
        schema={
            "__row_id__": pl.Int64,
            "__source__": pl.Utf8,
            "name": pl.Utf8,
            "email": pl.Utf8,
        },
    )


def _max_member_id(pairs_df) -> int:
    if pairs_df.is_empty():
        return 0
    return int(max(pairs_df["id_a"].max(), pairs_df["id_b"].max()))


def _peak_rss_mb() -> float:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _golden_rules():
    from goldenmatch.config.schemas import GoldenRulesConfig
    return GoldenRulesConfig(default_strategy="most_complete")


def _run_child(variant: str, n_pairs: int, runs: int) -> int:
    """Drive the real cluster -> golden -> identity stage for one variant, once
    per measured run, segmenting the wall into build / golden / identity."""

    import polars as pl  # noqa: F401
    from goldenmatch.core.cluster import (
        build_cluster_frames,
        build_clusters,
    )
    from goldenmatch.core.cluster_pairscores import ClusterPairScores
    from goldenmatch.core.golden import (
        build_golden_records_batch,
        build_golden_records_from_frames,
    )
    # NOTE: identity is measured as its CLUSTER-REPRESENTATION prep only -- the
    # ClusterPairScores view (from_frames vs from_pairs) + the per-cluster member
    # iteration SP-C changed. The full resolve_clusters store I/O is DELIBERATELY
    # excluded: it's a shared, frames-invariant confound that dominates wall (near-
    # quadratic SQLite upserts at scale) and masks the cluster-representation RSS
    # delta this bench measures. Identity byte-identical parity is already proven by
    # the SP-C gates; here we only need the representation cost.

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)
    actual_pairs = pairs_df.height
    n_members = _max_member_id(pairs_df) + 1
    source_df = _make_source_df(n_members)
    rules = _golden_rules()

    columnar = variant == "columnar"
    if columnar:
        os.environ["GOLDENMATCH_CLUSTER_FRAMES_OUT"] = "1"
    else:
        os.environ.pop("GOLDENMATCH_CLUSTER_FRAMES_OUT", None)
        os.environ.pop("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", None)

    def _one_run() -> tuple[float, float, float]:
        """Returns (build_s, golden_s, id_prep_s) for a single stage pass.

        id_prep = the identity CLUSTER-REPRESENTATION work SP-C changed: the
        ClusterPairScores view (from_frames vs from_pairs) + the per-cluster
        member iteration the resolver loop drives (frames group_by vs
        clusters.items()). NO IdentityStore / resolve_clusters: the store I/O is a
        shared, frames-invariant confound (near-quadratic SQLite upserts at scale)
        that masks the representation delta. Peak RSS over build+golden+id_prep is
        the cluster-representation cost -- the verdict axis.
        """
        if columnar:
            # --- build (frames) ---
            t0 = time.perf_counter()
            frames = build_cluster_frames(
                pairs_list,
                all_ids=None,
                max_cluster_size=100,
                weak_cluster_threshold=0.3,
                auto_split=True,
            )
            build_s = time.perf_counter() - t0

            # --- golden (from frames) ---
            t1 = time.perf_counter()
            build_golden_records_from_frames(
                source_df, frames, rules,
                quality_scores=None, provenance=False,
            )
            golden_s = time.perf_counter() - t1

            # --- identity cluster-rep prep (view + per-cluster member iter) ---
            t2 = time.perf_counter()
            view = ClusterPairScores.from_frames(frames.assignments, pairs_list)
            _agg = frames.assignments.group_by("cluster_id").agg(pl.col("member_id"))
            members_by_cid = dict(
                zip(_agg["cluster_id"].to_list(), _agg["member_id"].to_list())
            )
            id_prep_s = time.perf_counter() - t2
            _keepalive = (frames, view, members_by_cid)  # hold the rep for peak RSS
            del _keepalive
        else:
            # --- build (dict) ---
            t0 = time.perf_counter()
            clusters = build_clusters(
                pairs_list,
                max_cluster_size=100,
                weak_cluster_threshold=0.3,
                auto_split=True,
            )
            build_s = time.perf_counter() - t0

            # --- golden (dict -> multi_df) ---
            t1 = time.perf_counter()
            eligible = [
                (cid, info) for cid, info in clusters.items()
                if info["size"] > 1 and not info["oversized"]
            ]
            row_to_cluster: dict[int, int] = {}
            for cid, info in eligible:
                for mid in info["members"]:
                    row_to_cluster[mid] = cid
            if row_to_cluster:
                multi_df = source_df.filter(
                    pl.col("__row_id__").is_in(list(row_to_cluster.keys()))
                ).with_columns(
                    pl.col("__row_id__").replace_strict(
                        list(row_to_cluster.keys()),
                        list(row_to_cluster.values()),
                        return_dtype=pl.Int64,
                    ).alias("__cluster_id__")
                )
                build_golden_records_batch(multi_df, rules, provenance=False)
            golden_s = time.perf_counter() - t1

            # --- identity cluster-rep prep (view + clusters.items() iter) ---
            t2 = time.perf_counter()
            view = ClusterPairScores.from_pairs(pairs_list, clusters)
            members_by_cid = {cid: info["members"] for cid, info in clusters.items()}
            id_prep_s = time.perf_counter() - t2
            _keepalive = (clusters, view, members_by_cid)  # hold the rep for peak RSS
            del _keepalive
        return build_s, golden_s, id_prep_s

    _one_run()  # warm

    build_walls: list[float] = []
    golden_walls: list[float] = []
    identity_walls: list[float] = []
    for _ in range(runs):
        b, g, i = _one_run()
        build_walls.append(b)
        golden_walls.append(g)
        identity_walls.append(i)

    print(json.dumps({
        "variant": variant,
        "n_pairs": actual_pairs,
        "n_members": n_members,
        "build_walls": build_walls,
        "golden_walls": golden_walls,
        "identity_walls": identity_walls,
        "peak_rss_mb": _peak_rss_mb(),
    }), flush=True)
    return 0


def _membership_sanity(n_pairs: int) -> tuple[bool, int, int]:
    """Cheap membership check: columnar cluster count == legacy cluster count.
    NOT a parity gate (SP-A/B/C already proved byte-identical) -- a smoke that
    the two variants build the same partition before we trust their perf."""
    from goldenmatch.core.cluster import (
        build_cluster_frames,
        build_clusters,
    )

    pairs_df = _make_pairs_df(n_pairs)
    pairs_list = _pairs_list_from_df(pairs_df)

    os.environ.pop("GOLDENMATCH_CLUSTER_FRAMES_OUT", None)
    os.environ.pop("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", None)
    clusters = build_clusters(
        pairs_list,
        max_cluster_size=100,
        weak_cluster_threshold=0.3,
        auto_split=True,
    )
    legacy_count = len(clusters)

    os.environ["GOLDENMATCH_CLUSTER_FRAMES_OUT"] = "1"
    frames = build_cluster_frames(
        pairs_list,
        all_ids=None,
        max_cluster_size=100,
        weak_cluster_threshold=0.3,
        auto_split=True,
    )
    columnar_count = frames.metadata.height
    os.environ.pop("GOLDENMATCH_CLUSTER_FRAMES_OUT", None)
    return (columnar_count == legacy_count), legacy_count, columnar_count


def _bench_variant(variant: str, n: int, runs: int) -> dict[str, Any]:
    """Run a single variant in a child process. Returns a parsed result dict, or
    a ``{"oom": True}`` marker when the child OOMs / exits non-zero (the 100M
    legacy leg)."""
    cmd = [
        sys.executable, os.path.abspath(__file__),
        "--child", variant, "--np", str(n), "--runs", str(runs),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    last_json = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            last_json = line
    if proc.returncode != 0 or last_json is None:
        # OOM / SIGKILL (-9) / any non-zero exit, or no JSON emitted -> treat as
        # an infeasibility data point, NOT a crash of the whole bench.
        return {
            "oom": True,
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr.strip()[-2000:],
        }
    return json.loads(last_json)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", default="1000000,5000000",
                    help="Comma-separated target pair counts")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--output", default=None)
    ap.add_argument("--child", choices=["legacy", "columnar"], default=None,
                    help="Internal: run a single variant in this process")
    args = ap.parse_args()

    runs = max(1, args.runs)

    if args.child is not None:
        nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]
        n = nps[0] if nps else 1000000
        return _run_child(args.child, n, runs)

    nps = [int(x.strip()) for x in args.np.split(",") if x.strip()]

    from goldenmatch.core._native_loader import native_available, native_module
    print(f"native importable: {native_available()}", flush=True)
    m = native_module()
    print(f"build_clusters_arrow exposed: "
          f"{bool(m) and hasattr(m, 'build_clusters_arrow')}", flush=True)

    print("recorded DATA; binding Phase-2 verdict (legacy dict vs columnar "
          "A+B+C complete path).", flush=True)
    print("NOTE: id_prep = identity cluster-rep prep (view + per-cluster iter) "
          "ONLY; the full resolve_clusters + IdentityStore I/O is EXCLUDED "
          "(shared, frames-invariant, near-quadratic at scale). The >=30% RSS "
          "criterion applies to the cluster-representation delta (build + golden "
          "+ id_prep), plus the 100M legacy-OOM feasibility.", flush=True)

    sane_n = 2000
    print(f"membership sanity (np={sane_n:,}) ...", flush=True)
    ok, legacy_count, columnar_count = _membership_sanity(sane_n)
    if ok:
        print(f"membership OK (both {legacy_count:,} clusters)", flush=True)
    else:
        # Not a kill gate -- record it and proceed; parity is already proven by
        # the SP-A/B/C gates, this is only a smoke.
        print(f"membership MISMATCH: legacy={legacy_count:,} "
              f"columnar={columnar_count:,} (proceeding; perf still recorded)",
              flush=True)

    results = []
    for n in nps:
        print(f"  target_pairs={n:,} ...", flush=True)
        try:
            legacy = _bench_variant("legacy", n, runs)
            columnar = _bench_variant("columnar", n, runs)
            row: dict[str, Any] = {"n_pairs": n}

            if columnar.get("oom"):
                # rc in (-9, 137) is a real OOM (SIGKILL). Any other non-zero is a
                # CODE ERROR -- surface the child stderr so it isn't mislabeled.
                _rc = columnar.get("returncode")
                _kind = "OOM" if _rc in (-9, 137) else "ERROR"
                row["columnar"] = {"oom": True, "returncode": _rc, "kind": _kind}
                print(f"    pairs={n:,}  columnar {_kind} (rc={_rc})", flush=True)
                if _kind == "ERROR":
                    print(f"--- columnar child stderr ---\n"
                          f"{columnar.get('stderr_tail', '')}", flush=True)
            else:
                row["n_pairs"] = columnar["n_pairs"]
                row["columnar"] = {
                    "build_s": statistics.median(columnar["build_walls"]),
                    "golden_s": statistics.median(columnar["golden_walls"]),
                    "identity_s": statistics.median(columnar["identity_walls"]),
                    "peak_rss_mb": columnar["peak_rss_mb"],
                }

            if legacy.get("oom"):
                _rc = legacy.get("returncode")
                _kind = "OOM" if _rc in (-9, 137) else "ERROR"
                row["legacy"] = {"oom": True, "returncode": _rc, "kind": _kind}
                print(f"    pairs={n:,}  legacy {_kind} (rc={_rc})", flush=True)
                if _kind == "ERROR":
                    print(f"--- legacy child stderr ---\n"
                          f"{legacy.get('stderr_tail', '')}", flush=True)
            else:
                row["legacy"] = {
                    "build_s": statistics.median(legacy["build_walls"]),
                    "golden_s": statistics.median(legacy["golden_walls"]),
                    "identity_s": statistics.median(legacy["identity_walls"]),
                    "peak_rss_mb": legacy["peak_rss_mb"],
                }

            results.append(row)

            def _fmt(v: dict[str, Any]) -> str:
                if v.get("oom"):
                    return "OOM"
                return (f"build={v['build_s']:.3f} golden={v['golden_s']:.3f} "
                        f"identity={v['identity_s']:.3f} "
                        f"rss={v['peak_rss_mb']:.1f}MB")

            print(f"    legacy:   {_fmt(row['legacy'])}", flush=True)
            print(f"    columnar: {_fmt(row['columnar'])}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  target_pairs={n:,}  ERROR {type(exc).__name__}: {exc}",
                  flush=True)
            results.append({"n_pairs": n, "error": str(exc)})

    def _cell(v: dict[str, Any] | None) -> tuple[str, str, str, str]:
        if v is None or "build_s" not in v:
            tag = "OOM" if (v and v.get("oom")) else "n/a"
            return tag, tag, tag, tag
        return (
            f"{v['build_s']:.3f}", f"{v['golden_s']:.3f}",
            f"{v['identity_s']:.3f}", f"{v['peak_rss_mb']:.1f}",
        )

    lines = [
        "\n## bench-pipeline-complete-path\n",
        "Per-variant stage wall (build / golden / id_prep seconds) + overall "
        "peak RSS. legacy = dict; columnar = frames-out (A+B+C). id_prep = "
        "identity cluster-rep prep (view + iter); full store resolution "
        "EXCLUDED (shared, frames-invariant, near-quadratic).\n",
        f"| {'pairs':>12} | {'variant':>8} | {'build s':>9} | {'golden s':>9} "
        f"| {'id_prep s':>10} | {'peak RSS MB':>12} |",
        f"| {'-'*12} | {'-'*8} | {'-'*9} | {'-'*9} | {'-'*10} | {'-'*12} |",
    ]
    for r in results:
        if "error" in r:
            lines.append(
                f"| {r['n_pairs']:>12,} | {'ERROR':>8} | {'ERROR':>9} | "
                f"{'ERROR':>9} | {'ERROR':>10} | {'n/a':>12} |"
            )
            continue
        for variant in ("legacy", "columnar"):
            b, g, i, rss = _cell(r.get(variant))
            lines.append(
                f"| {r['n_pairs']:>12,} | {variant:>8} | {b:>9} | {g:>9} | "
                f"{i:>10} | {rss:>12} |"
            )
    table = "\n".join(lines)
    print(table, flush=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(table + "\n")
        except OSError:
            pass
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump({"results": results}, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
