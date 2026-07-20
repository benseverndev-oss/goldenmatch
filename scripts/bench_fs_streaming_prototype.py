#!/usr/bin/env python
"""De-risking prototype: does keeping FS prepared records RESIDENT IN DUCKDB and
streaming block-groups through the native FS kernel bound peak RSS, vs the
current all-frame-resident bucket path?

Faithful by construction: we CAPTURE the exact (sorted_bucket_df row order,
size_list) the real pipeline hands the kernel, plus the prepared frame and the
trained em_result/mk. Then we REPLAY the identical blocks through the identical
kernel two ways, each in its own process for a clean peak-RSS reading:

  replay-resident : hold the whole prepared frame in RAM (today's model),
                    gather each bucket's rows from it, score.
  replay-duckdb   : load prepared frame into a DuckDB table, DROP the frame,
                    SELECT each bucket's rows on demand, score.

Same kernel + same blocks => identical pair set (asserted). The only variable is
where the frame lives. If duckdb's peak stays ~flat while resident climbs with N,
the Splink-style frame-in-DuckDB bet is real and worth a spec.

Usage:
  python bench_fs_streaming_prototype.py capture         <fixture.parquet> <workdir>
  python bench_fs_streaming_prototype.py replay-resident <workdir>
  python bench_fs_streaming_prototype.py replay-duckdb    <workdir>

Design: docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md
"""
from __future__ import annotations

import gc
import json
import os
import pickle
import sys
import threading
import time
from pathlib import Path

# FS lane env, identical to the bench (must precede goldenmatch import).
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
os.environ["GOLDENMATCH_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_CALIBRATED"] = "posterior"
os.environ["GOLDENMATCH_FS_BLOCKING_SN_BOUND"] = "1"
os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
os.environ.setdefault("GOLDENMATCH_FS_EM_SAMPLE_ROWS", "100000")

import resource


def _vmrss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


class Sampler(threading.Thread):
    def __init__(self, interval=0.03):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0.0
        self._ev = threading.Event()

    def run(self):
        while not self._ev.is_set():
            self.peak = max(self.peak, _vmrss_mb())
            time.sleep(self.interval)

    def halt(self):
        self._ev.set()
        self.join(timeout=1)


def _ru_peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------- capture ----
def capture(fixture: str, workdir: str) -> None:
    import polars as pl
    import pyarrow.parquet as pq

    import goldenmatch.backends.score_buckets as sb
    import goldenmatch.core.probabilistic as prob
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    try:
        from goldenmatch import dedupe_df
    except ImportError:
        from goldenmatch._api import dedupe_df

    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)

    df = pq.read_table(fixture)
    cfg = auto_configure_probabilistic_df(df)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) == "weighted":
            mk.rerank = False

    captured: dict = {"buckets": [], "prepared_paths": [], "mk": None, "em": None}

    # Wrap score_buckets to stash the prepared frame + mk + em_result it receives.
    real_score_buckets = sb.score_buckets

    def wrapped_score_buckets(prepared_df, blocking_config, mk, matched_pairs,
                              *args, em_result=None, **kwargs):
        if captured["mk"] is None:
            captured["mk"] = mk
            captured["em"] = em_result
        return real_score_buckets(prepared_df, blocking_config, mk,
                                  matched_pairs, *args,
                                  em_result=em_result, **kwargs)

    # ONE wrapper: normalize sorted_bucket_df to polars, record (row order,
    # size_list) AND the frame (its union = the prepared frame whose residency
    # is the whole question), then call the real kernel with the ORIGINAL arg.
    real_kernel = prob.score_probabilistic_bucket_native
    frames: list = []

    def wrapped_kernel(sorted_bucket_df, size_list, mk, em_result,
                       exclude_pairs=None, exclude_handle=None):
        try:
            fr = sorted_bucket_df
            pf = fr if isinstance(fr, pl.DataFrame) else pl.from_arrow(fr)
            frames.append(pf)
            rids = pf["__row_id__"].to_list()
            captured["buckets"].append(
                {"row_ids": [int(r) for r in rids],
                 "sizes": [int(s) for s in size_list]}
            )
        except Exception as e:  # never break the real run
            captured.setdefault("capture_errors", []).append(repr(e))
        return real_kernel(sorted_bucket_df, size_list, mk, em_result,
                           exclude_pairs, exclude_handle)

    # Patch BOTH the definition module and the name score_buckets imported.
    sb.score_buckets = wrapped_score_buckets
    prob.score_probabilistic_bucket_native = wrapped_kernel
    sb.score_probabilistic_bucket_native = wrapped_kernel  # imported alias

    t0 = time.perf_counter()
    ded = dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0

    if not frames:
        print("NO kernel frames captured -- FS native path did not engage. "
              "Check GOLDENMATCH_FS_NATIVE / eligibility.")
        print("capture_errors:", captured.get("capture_errors"))
        sys.exit(2)

    prepared = pl.concat(frames, how="vertical_relaxed")
    prepared = prepared.unique(subset=["__row_id__"], keep="first")
    prepared.write_parquet(wd / "prepared.parquet")

    (wd / "blocks.json").write_text(json.dumps(captured["buckets"]))
    with open(wd / "mkem.pkl", "wb") as fh:
        pickle.dump({"mk": captured["mk"], "em": captured["em"]}, fh)

    n_pairs = sum(len(b["sizes"]) for b in captured["buckets"])
    total_rows = sum(sum(b["sizes"]) for b in captured["buckets"])
    print(f"captured: {len(captured['buckets'])} bucket-calls, "
          f"{n_pairs} blocks, {total_rows} scored rows, "
          f"prepared cols={prepared.width}, prepared rows={prepared.height}")
    print(f"real dedupe wall={wall:.1f}s  peak_ru={_ru_peak_mb():.0f}MB")


# ---------------------------------------------------------- shared replay ----
def _load(workdir: str):
    import pickle as pk

    wd = Path(workdir)
    blocks = json.loads((wd / "blocks.json").read_text())
    with open(wd / "mkem.pkl", "rb") as fh:
        mkem = pk.load(fh)
    return wd, blocks, mkem["mk"], mkem["em"]


def _score_blocks(get_bucket_frame, blocks, mk, em) -> set:
    """get_bucket_frame(row_ids)->polars frame in row_ids order. Returns pair set."""
    from goldenmatch.core.probabilistic import score_probabilistic_bucket_native

    pairs: set = set()
    for b in blocks:
        rids = b["row_ids"]
        sizes = b["sizes"]
        frame = get_bucket_frame(rids)  # ordered to match rids
        got = score_probabilistic_bucket_native(
            frame, sizes, mk, em, None, None
        )
        for a, c, s in got:
            key = (a, c) if a < c else (c, a)
            pairs.add((key[0], key[1], round(float(s), 4)))
    return pairs


# ------------------------------------------------------ replay: resident ----
def replay_resident(workdir: str) -> None:
    import polars as pl

    wd, blocks, mk, em = _load(workdir)
    sampler = Sampler(); sampler.start()

    prepared = pl.read_parquet(wd / "prepared.parquet")  # RESIDENT for the run
    rss_loaded = _vmrss_mb()

    def get_bucket_frame(rids):
        order = pl.DataFrame({"__row_id__": rids})
        return order.join(prepared, on="__row_id__", how="left")

    t0 = time.perf_counter()
    pairs = _score_blocks(get_bucket_frame, blocks, mk, em)
    wall = time.perf_counter() - t0
    sampler.halt()

    (wd / "pairs_resident.json").write_text(
        json.dumps(sorted(f"{a}-{b}-{s}" for a, b, s in pairs))
    )
    print(f"[resident] pairs={len(pairs)} wall={wall:.1f}s "
          f"rss_after_load={rss_loaded:.0f}MB "
          f"peak_sampled={sampler.peak:.0f}MB peak_ru={_ru_peak_mb():.0f}MB")


# --------------------------------------------------------- replay: duckdb ----
def replay_duckdb(workdir: str) -> None:
    import duckdb
    import polars as pl

    wd, blocks, mk, em = _load(workdir)
    sampler = Sampler(); sampler.start()

    # Load prepared into DuckDB, then DROP the polars frame: the frame now lives
    # OUT of the driver's Python heap. Only one bucket at a time comes back.
    prepared = pl.read_parquet(wd / "prepared.parquet")
    con = duckdb.connect(str(wd / "prep.duckdb"))
    con.execute("DROP TABLE IF EXISTS prep")
    con.register("prep_arrow", prepared.to_arrow())
    con.execute("CREATE TABLE prep AS SELECT * FROM prep_arrow")
    con.unregister("prep_arrow")
    con.execute("CREATE INDEX idx_rid ON prep(__row_id__)")
    del prepared
    gc.collect()
    rss_loaded = _vmrss_mb()

    def get_bucket_frame(rids):
        # Pull just this bucket's rows; reorder to rids so size_list slicing is
        # block-contiguous (kernel only compares within-block, but the runs must
        # line up with size_list).
        arr = con.execute(
            "SELECT * FROM prep WHERE __row_id__ IN "
            f"({','.join(str(int(r)) for r in rids)})"
        ).arrow()
        got = pl.from_arrow(arr)
        order = pl.DataFrame({"__row_id__": rids})
        return order.join(got, on="__row_id__", how="left")

    t0 = time.perf_counter()
    pairs = _score_blocks(get_bucket_frame, blocks, mk, em)
    wall = time.perf_counter() - t0
    sampler.halt()

    (wd / "pairs_duckdb.json").write_text(
        json.dumps(sorted(f"{a}-{b}-{s}" for a, b, s in pairs))
    )
    print(f"[duckdb]   pairs={len(pairs)} wall={wall:.1f}s "
          f"rss_after_load={rss_loaded:.0f}MB "
          f"peak_sampled={sampler.peak:.0f}MB peak_ru={_ru_peak_mb():.0f}MB")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "capture":
        capture(sys.argv[2], sys.argv[3])
    elif cmd == "replay-resident":
        replay_resident(sys.argv[2])
    elif cmd == "replay-duckdb":
        replay_duckdb(sys.argv[2])
    else:
        print(__doc__); sys.exit(1)
