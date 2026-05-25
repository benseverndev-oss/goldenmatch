"""Block-scoring native-kernel bench (Phase 2 confirmation at scale).

Isolates the one stage the native kernel changes — bucket block-scoring — and
measures the native kernel (rapidfuzz-rs + rayon): `bucket_score` wall, emitted
pairs, peak RSS. The pure-Python baseline is opt-in (`--with-python`) for the
speedup ratio + pair-set parity check; it's left OFF by default because it's
O(pairs), quadratic in block size, and intractable on the full Zipfian-surname
fixture (the speedup + parity are locked by the unit tests and smaller-scale
runs). At scale, this bench confirms native's absolute wall and that it
completes.

It deliberately does NOT run the full dedupe (auto-config / clustering / golden
are unchanged by this kernel and would just add ~50 min × 2 of noise). For an
end-to-end 5M run use `scripts/scale_audit_5m.py`.

Config is explicit (not auto-config) so the native fast path is guaranteed to
engage: a weighted matchkey on names+email with native-eligible scorers
(jaro_winkler / token_sort) and transforms (so `_resolve_fast_path` finds the
precomputed columns). Auto-config may add negative-evidence, which disqualifies
the fast path — fine in production, wrong for a controlled kernel bench.

Accepts a `.parquet` (e.g. the reusable `bench_<rows>.parquet` from the
`bench-dataset-v1` release) or a `.csv` fixture; the columns it needs are
`first_name`, `last_name`, `email` (the person shape both generators emit).

Run (after building the ext):
    uv run python scripts/build_native.py
    uv run python scripts/bench_native_bucket.py \
        --fixture bench-dataset/bench_5000000.parquet \
        --output .profile_tmp/native_bucket_result.json \
        --summary-md "$GITHUB_STEP_SUMMARY"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def _poll_rss_mb(stop: threading.Event, peak: list[float]) -> None:
    """Sample current RSS until stopped; record the high-water mark. No-op
    without psutil."""
    try:
        import psutil
    except ImportError:
        return
    proc = psutil.Process()
    while not stop.is_set():
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        if rss_mb > peak[0]:
            peak[0] = rss_mb
        stop.wait(0.25)


def _pairset_digest(pairs: list[tuple[int, int, float]]) -> tuple[int, str]:
    """(count, sha256) over the canonical (min,max) pair SET. Scores are
    excluded on purpose: rapidfuzz-rs vs Python can differ sub-ULP, but the
    emitted pair set is the parity contract."""
    canon = sorted({(min(a, b), max(a, b)) for a, b, _s in pairs})
    h = hashlib.sha256()
    for a, b in canon:
        h.update(f"{a},{b};".encode())
    return len(canon), h.hexdigest()


def _run_one(native: str, prepared, blocking, mk, n_buckets: int) -> dict[str, Any]:
    """Run score_buckets once under the given GOLDENMATCH_NATIVE value."""
    from goldenmatch.backends.score_buckets import score_buckets
    from goldenmatch.core.bench import bench_capture
    from goldenmatch.core._native_loader import native_enabled

    os.environ["GOLDENMATCH_NATIVE"] = native
    label = "native" if native == "1" else "python"
    enabled = native_enabled("block_scoring")
    print(f"[bench] {label}: GOLDENMATCH_NATIVE={native} block_scoring_enabled={enabled}", flush=True)

    peak = [0.0]
    stop = threading.Event()
    poller = threading.Thread(target=_poll_rss_mb, args=(stop, peak), daemon=True)
    poller.start()

    t0 = time.perf_counter()
    with bench_capture() as bench:
        pairs = score_buckets(prepared, blocking, mk, set(), n_buckets=n_buckets)
    wall = time.perf_counter() - t0

    stop.set()
    poller.join(timeout=2)

    bucket_score_s = bench.to_dict().get("timings", {}).get("bucket_score", wall)
    count, digest = _pairset_digest(pairs)
    print(f"[bench] {label}: bucket_score={bucket_score_s:.2f}s wall={wall:.2f}s "
          f"pairs={count} peak_rss={peak[0]:.0f}MB", flush=True)
    return {
        "label": label,
        "native": native,
        "block_scoring_enabled": enabled,
        "bucket_score_s": round(bucket_score_s, 3),
        "wall_s": round(wall, 3),
        "pair_count": count,
        "pairset_sha256": digest,
        "peak_rss_mb": round(peak[0], 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True,
                        help="Person-like CSV (from scale_audit_5m_generate.py)")
    parser.add_argument("--block-key", default="last_name",
                        help="Blocking key column (default last_name)")
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--n-buckets", type=int, default=0,
                        help="0 -> score_buckets default (min(cpu*4, 1024))")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--summary-md", type=Path, default=None)
    parser.add_argument(
        "--with-python", action="store_true",
        help="Also run the pure-Python baseline for speedup + pair-set parity. "
             "Off by default: the Python loop is O(pairs), quadratic in block "
             "size, so it's intractable on the full Zipfian-surname fixture. "
             "Use it on a small fixture to re-check parity/speedup; at scale "
             "this bench measures native alone (parity is locked by the unit "
             "tests + smaller-scale runs).",
    )
    args = parser.parse_args()

    import polars as pl
    from goldenmatch.config.schemas import (
        BlockingConfig, BlockingKeyConfig, MatchkeyConfig, MatchkeyField,
    )
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    print(f"[bench] loading {args.fixture}", flush=True)
    df = (
        pl.read_parquet(args.fixture)
        if args.fixture.suffix == ".parquet"
        else pl.read_csv(args.fixture)
    )
    df = df.with_row_index("__row_id__").with_columns(pl.col("__row_id__").cast(pl.Int64))
    print(f"[bench] rows={df.height:,} cols={df.width}", flush=True)

    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=[args.block_key])])
    mk = MatchkeyConfig(
        name="person", type="weighted", threshold=args.threshold,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.4, transforms=["lowercase"]),
            MatchkeyField(field="last_name", scorer="jaro_winkler", weight=0.3, transforms=["lowercase"]),
            MatchkeyField(field="email", scorer="token_sort", weight=0.3, transforms=["lowercase"]),
        ],
    )
    df = precompute_matchkey_transforms(df, [mk])
    n_buckets = args.n_buckets or (min((os.cpu_count() or 4) * 4, 1024))

    try:
        import goldenmatch._native as _n
        ext_version = _n.__version__
    except Exception:
        ext_version = "not-built"

    # Native is always measured. The pure-Python baseline is opt-in
    # (--with-python) because it's O(pairs), quadratic in block size, and
    # intractable on the full Zipfian-surname fixture. Speedup + parity are
    # locked by the unit tests + smaller-scale runs; at scale this confirms
    # native's absolute wall and that it completes.
    nat = _run_one("1", df, blocking, mk, n_buckets)
    py = _run_one("0", df, blocking, mk, n_buckets) if args.with_python else None

    parity_ok = None
    speedup = None
    if py is not None:
        parity_ok = py["pairset_sha256"] == nat["pairset_sha256"]
        speedup = (py["bucket_score_s"] / nat["bucket_score_s"]) if nat["bucket_score_s"] else 0.0

    result = {
        "rows": df.height,
        "n_buckets": n_buckets,
        "block_key": args.block_key,
        "threshold": args.threshold,
        "ext_version": ext_version,
        "native": nat,
        "python": py,
        "parity_pairset_identical": parity_ok,
        "bucket_score_speedup": (round(speedup, 2) if speedup is not None else None),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2))
        print(f"[bench] wrote {args.output}", flush=True)

    table_rows = [
        f"| native (rapidfuzz-rs + rayon) | {nat['bucket_score_s']}s | {nat['wall_s']}s | {nat['pair_count']} | {nat['peak_rss_mb']}MB |",
    ]
    if py is not None:
        table_rows.insert(
            0,
            f"| python (rapidfuzz loop) | {py['bucket_score_s']}s | {py['wall_s']}s | {py['pair_count']} | {py['peak_rss_mb']}MB |",
        )
    lines = [
        "## Native block-scoring bench", "",
        f"- rows: **{df.height:,}**  buckets: {n_buckets}  block_key: `{args.block_key}`  ext: {ext_version}",
        "",
        "| path | bucket_score | wall | pairs | peak RSS |",
        "|---|---|---|---|---|",
        *table_rows,
    ]
    if py is not None:
        lines += [
            "",
            f"- **bucket_score speedup: {speedup:.2f}x**",
            f"- pair-set parity: {'PASS (identical)' if parity_ok else 'FAIL (pair sets differ!)'}",
        ]
    text = "\n".join(lines) + "\n"
    if args.summary_md and str(args.summary_md) != "-":
        with args.summary_md.open("a", encoding="utf-8") as f:
            f.write(text)
    print(text)

    if py is not None and not parity_ok:
        print("[bench] ERROR: native and python emitted different pair sets", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
