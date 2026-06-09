"""GH Actions hotspot profiler — identifies *where* wall time goes in
the pair-stream / cluster / golden path, so the next optimization
round can target real hotspots instead of guessing.

Same shape as the existing pair-stream bench (``bench_pair_stream_columnar.py``):
- subprocess-isolated per (shape, target) so one OOM doesn't poison
  the others
- runs against the realistic_person fixture (Phase 0)
- emits JSON + per-run artifacts the workflow uploads

Two profilers, depending on what you want to see:

- ``pyinstrument`` (default): statistical sampler, ~5% overhead, HTML
  flame-graph output. Best for "where is the wall actually going"
  questions across the full pipeline. Doesn't double-count async/
  thread-pool work the way cProfile does.
- ``cprofile``: exact per-function timing via the deterministic
  Python profiler. Higher overhead (~30-40%), but gives a per-call
  cumtime that's authoritative when you want to compare specific
  function-call counts.

Two targets you can profile:

- ``list``: legacy ``score_blocks_parallel`` -> ``build_clusters``
  path. The pre-Phase-1c baseline against which the columnar
  speedup was measured.
- ``columnar``: Phase 1c-real ``score_blocks_columnar`` ->
  ``build_clusters_columnar`` path. The post-#639 winner that
  measured 22.7% faster at 1M.

Run via the ``profile-hotspots`` workflow on ``large-new-64GB``.
Don't run locally past 100K -- memory/feedback_avoid_full_suite_oom
applies.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

# Reuse the Phase-0 realistic-person fixture.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures.realistic_person import realistic_person_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks  # noqa: E402
from goldenmatch.core.cluster import (  # noqa: E402
    build_clusters,
    build_clusters_columnar,
)
from goldenmatch.core.scorer import (  # noqa: E402
    score_blocks_columnar,
    score_blocks_parallel,
)

# ── Config builder (mirrors bench_pair_stream_columnar) ─────────────


def _make_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[
                    MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
                ],
                threshold=0.85,
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


def _prepare_blocks(df: pl.DataFrame, cfg: GoldenMatchConfig) -> tuple[list, list[int]]:
    prepped = df.with_columns(pl.lit("fixture").alias("__source__"))
    if "__row_id__" not in prepped.columns:
        prepped = prepped.with_row_index(name="__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    blocks = build_blocks(prepped.lazy(), cfg.blocking)
    return blocks, prepped["__row_id__"].to_list()


# ── Profile targets ─────────────────────────────────────────────────


def _profile_list_path(blocks: list, cfg: GoldenMatchConfig, all_ids: list[int]) -> tuple[int, int, float]:
    """Returns (n_pairs, n_clusters, wall_s)."""
    mk = cfg.matchkeys[0]
    matched: set[tuple[int, int]] = set()
    t0 = time.perf_counter()
    pairs = score_blocks_parallel(blocks, mk, matched)
    clusters = build_clusters(pairs, all_ids=all_ids)
    wall = time.perf_counter() - t0
    return len(pairs), len(clusters), wall


def _profile_columnar_path(blocks: list, cfg: GoldenMatchConfig, all_ids: list[int]) -> tuple[int, int, float]:
    mk = cfg.matchkeys[0]
    matched: set[tuple[int, int]] = set()
    t0 = time.perf_counter()
    # Mirror the pipeline's columnar caller: eligibility guarantees a single
    # matchkey, so matched_pairs is never consumed -> track_matched=False
    # (the guard that eliminates the per-pair min/max/set.add hot spot).
    pairs_df = score_blocks_columnar(blocks, mk, matched, track_matched=False)
    clusters = build_clusters_columnar(pairs_df, all_ids=all_ids)
    wall = time.perf_counter() - t0
    return pairs_df.height, len(clusters), wall


_TARGETS = {
    "list": _profile_list_path,
    "columnar": _profile_columnar_path,
}


# ── Worker (one (shape, target, profiler) at a time, subprocess-isolated) ──


def _worker_main(n: int, target: str, profiler: str, out_dir: Path) -> int:
    """Run one profile in this subprocess. Writes:

    - ``<target>_n<n>_<profiler>.json``: summary (wall, n_pairs, n_clusters,
      profiler, top hotspots if available)
    - ``<target>_n<n>_<profiler>.txt``: human-readable profile dump
      (pyinstrument console output, or cProfile pstats)
    - ``<target>_n<n>_pyinstrument.html``: pyinstrument flame graph
      (only when profiler=pyinstrument)
    """
    if target not in _TARGETS:
        print(f"unknown target: {target}", file=sys.stderr, flush=True)
        return 2
    target_fn = _TARGETS[target]

    df = realistic_person_df(n)
    if "__row_id__" not in df.columns:
        df = df.with_row_index(name="__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64),
        )
    cfg = _make_config()
    blocks, all_ids = _prepare_blocks(df, cfg)

    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{target}_n{n}_{profiler}"
    summary: dict = {
        "n": n,
        "target": target,
        "profiler": profiler,
        "fixture_height": df.height,
        "n_blocks": len(blocks),
    }

    if profiler == "pyinstrument":
        try:
            from pyinstrument import Profiler  # type: ignore[import-not-found]
        except ImportError:
            print(
                "pyinstrument not installed; install with: pip install pyinstrument",
                file=sys.stderr, flush=True,
            )
            return 3

        prof = Profiler(interval=0.001)
        prof.start()
        n_pairs, n_clusters, wall = target_fn(blocks, cfg, all_ids)
        prof.stop()

        # Console output (human readable) -- shows the top frame chain
        console_text = prof.output_text(unicode=True, color=False)
        (base.with_suffix(".txt")).write_text(console_text, encoding="utf-8")

        # HTML flame graph for browsing
        html_text = prof.output_html()
        (base.with_suffix(".html")).write_text(html_text, encoding="utf-8")

        summary.update({
            "wall_s": wall,
            "n_pairs": n_pairs,
            "n_clusters": n_clusters,
            "console_path": str(base.with_suffix(".txt").name),
            "html_path": str(base.with_suffix(".html").name),
        })

    elif profiler == "cprofile":
        prof = cProfile.Profile()
        prof.enable()
        n_pairs, n_clusters, wall = target_fn(blocks, cfg, all_ids)
        prof.disable()

        # Dump pstats binary AND a human-readable cumtime-sorted dump.
        pstats_path = base.with_suffix(".pstats")
        prof.dump_stats(str(pstats_path))

        # Top 40 by cumulative time, then by total time.
        with (base.with_suffix(".txt")).open("w", encoding="utf-8") as fh:
            ps = pstats.Stats(prof, stream=fh)
            fh.write("===== Top 40 by cumulative time =====\n\n")
            ps.sort_stats("cumulative").print_stats(40)
            fh.write("\n===== Top 40 by total (own) time =====\n\n")
            ps.sort_stats("tottime").print_stats(40)

        # Extract top 10 hotspots into the JSON summary for the
        # workflow's markdown table.
        top_funcs: list[dict] = []
        ps = pstats.Stats(prof).sort_stats("cumulative")
        # ps.stats is dict[(file, lineno, name)] -> (cc, nc, tt, ct, callers)
        items = sorted(ps.stats.items(), key=lambda kv: kv[1][3], reverse=True)
        for (file, lineno, name), (cc, _nc, tt, ct, _callers) in items[:10]:
            top_funcs.append({
                "name": name,
                "file": Path(file).name,
                "line": lineno,
                "cumtime_s": ct,
                "tottime_s": tt,
                "ncalls": cc,
            })

        summary.update({
            "wall_s": wall,
            "n_pairs": n_pairs,
            "n_clusters": n_clusters,
            "pstats_path": str(pstats_path.name),
            "text_path": str(base.with_suffix(".txt").name),
            "top_cumtime": top_funcs,
        })

    else:
        print(f"unknown profiler: {profiler}", file=sys.stderr, flush=True)
        return 2

    (base.with_suffix(".json")).write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    print(f"__PROFILE_DONE__{base.name}", flush=True)
    return 0


# ── Orchestrator (spawn one subprocess per combo) ───────────────────


def _run_worker(
    n: int, target: str, profiler: str, out_dir: Path, script_path: Path,
) -> bool:
    cmd = [
        sys.executable, str(script_path),
        "--worker", str(n), target, profiler,
        "--out", str(out_dir),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"  {target}@{n:,} [{profiler}]: TIMEOUT", flush=True)
        return False

    if proc.returncode != 0:
        signal_hint = ""
        if proc.returncode in (137, -9):
            signal_hint = " (likely OOM-killed)"
        elif proc.returncode == 143:
            signal_hint = " (SIGTERM)"
        print(
            f"  {target}@{n:,} [{profiler}]: exit {proc.returncode}{signal_hint}",
            flush=True,
        )
        if proc.stderr:
            for line in proc.stderr.strip().splitlines()[-3:]:
                print(f"    stderr: {line}", flush=True)
        return False

    print(f"  {target}@{n:,} [{profiler}]: done", flush=True)
    return True


def _summarize(out_dir: Path) -> None:
    """Read each per-combo JSON and post a markdown table to stdout
    (the workflow captures this into the step summary)."""
    summaries: list[dict] = []
    for jpath in sorted(out_dir.glob("*.json")):
        try:
            summaries.append(json.loads(jpath.read_text()))
        except (OSError, json.JSONDecodeError):
            continue

    print("\n\n## Hotspot profile summary\n", flush=True)
    if not summaries:
        print("(no profile JSON files found)", flush=True)
        return
    print("| n | target | profiler | wall_s | n_pairs | n_clusters | report |", flush=True)
    print("|---:|---|---|---:|---:|---:|---|", flush=True)
    for s in summaries:
        report = s.get("html_path") or s.get("text_path") or "—"
        print(
            f"| {s.get('n', '?'):,} | {s.get('target', '?')} | "
            f"{s.get('profiler', '?')} | {s.get('wall_s', 0):.2f} | "
            f"{s.get('n_pairs', '?'):,} | {s.get('n_clusters', '?'):,} | "
            f"`{report}` |",
            flush=True,
        )

    # cProfile-only: show top hotspots for the largest shape.
    cprof = [s for s in summaries if s.get("profiler") == "cprofile"]
    if cprof:
        biggest = max(cprof, key=lambda s: s.get("n", 0))
        top = biggest.get("top_cumtime", [])
        if top:
            print(
                f"\n### Top 10 cumtime hotspots (cProfile @ "
                f"n={biggest['n']:,} {biggest['target']})\n",
                flush=True,
            )
            print("| function | file:line | cumtime_s | tottime_s | ncalls |", flush=True)
            print("|---|---|---:|---:|---:|", flush=True)
            for f in top:
                print(
                    f"| `{f['name']}` | {f['file']}:{f['line']} | "
                    f"{f['cumtime_s']:.2f} | {f['tottime_s']:.2f} | "
                    f"{f['ncalls']} |",
                    flush=True,
                )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--shapes", type=int, nargs="+", default=[100_000, 1_000_000],
        help="Row counts to profile.",
    )
    p.add_argument(
        "--targets", nargs="+", default=["list", "columnar"],
        choices=list(_TARGETS.keys()),
        help="Code paths to profile.",
    )
    p.add_argument(
        "--profilers", nargs="+", default=["pyinstrument", "cprofile"],
        choices=["pyinstrument", "cprofile"],
        help="Profilers to run.",
    )
    p.add_argument(
        "--out", type=Path,
        default=Path(__file__).resolve().parents[3] / ".profile_tmp" / "hotspots",
        help="Output directory for per-combo artifacts.",
    )
    p.add_argument(
        "--worker", nargs=3,
        metavar=("N", "TARGET", "PROFILER"),
        default=None,
        help="Internal: run a single (n, target, profiler) measurement "
             "and write its artifacts to --out. Used by the orchestrator "
             "to spawn isolated subprocesses.",
    )
    args = p.parse_args()

    script_path = Path(__file__).resolve()

    if args.worker is not None:
        n_str, target, profiler = args.worker
        return _worker_main(int(n_str), target, profiler, args.out)

    args.out.mkdir(parents=True, exist_ok=True)
    print(
        f"hotspot profiler: shapes={args.shapes} targets={args.targets} "
        f"profilers={args.profilers}, out={args.out}",
        flush=True,
    )
    for n in args.shapes:
        for target in args.targets:
            for profiler in args.profilers:
                print(
                    f"\n=== n={n:,} target={target} profiler={profiler} "
                    f"(subprocess-isolated) ===",
                    flush=True,
                )
                _run_worker(n, target, profiler, args.out, script_path)

    _summarize(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
