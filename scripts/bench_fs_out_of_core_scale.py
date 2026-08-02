#!/usr/bin/env python
"""Validate the single-box OUT-OF-CORE FS dedupe path at scale.

The question this answers: does `GOLDENMATCH_FS_OUT_OF_CORE=1` +
`dedupe_to_parquet` actually COMPLETE a 50M-row Fellegi-Sunter dedupe on a 64 GB
box, where the in-memory FS path OOMs (~82 GB projected)? And what is the real
peak RSS + wall?

One (rows, mode) datapoint per PROCESS so the OS reclaims memory between runs and
`ru_maxrss` is a clean high-water mark. The workflow runs each datapoint as an
isolated subprocess; an OOM-killed streaming run is itself a (negative) result.

Modes:
  streaming  -- dedupe_to_parquet(..., out_dir) with GOLDENMATCH_FS_OUT_OF_CORE=1.
                Asserts the streaming short-circuit ACTUALLY engaged
                (result["streaming"] is True) so a silent fallback to the
                in-memory path can't masquerade as a passing out-of-core run.
  in_memory  -- dedupe_df(full_frame, config): the contrast. EXPECTED to OOM at
                >=50M on 64 GB; recorded as such by the orchestrating workflow.

Fixture: the SAME person generator the ER head-to-head bench uses
(`generate_fixture.generate`), so the shape/dupes/FS-routability match the
in-memory scale numbers we're comparing against.

Usage:
  python bench_fs_out_of_core_scale.py --rows 50000000 --mode streaming \
      --workdir .bench_fs_ooc --out-json result.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

# --- FS-lane env BEFORE importing goldenmatch (native loader + planner read it) ---
# NB: deliberately do NOT set GOLDENMATCH_FS_BLOCKING_SN_BOUND -- the SN bound can
# emit a `sorted_neighborhood` blocking strategy, which the out-of-core scorer
# does not support (it falls back), and we want to exercise the static/multi_pass
# streaming path the eligibility gate covers.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
os.environ.setdefault("_RJEM_MALLOC_CONF", "dirty_decay_ms:1000,muzzy_decay_ms:0")
os.environ["GOLDENMATCH_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_CALIBRATED"] = "posterior"
os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
os.environ.setdefault("GOLDENMATCH_FS_EM_SAMPLE_ROWS", "100000")

import resource  # noqa: E402


def _vmrss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


class Sampler(threading.Thread):
    def __init__(self, interval: float = 0.1, heartbeat_s: float = 30.0,
                 label: str = ""):
        super().__init__(daemon=True)
        self.interval = interval
        self.heartbeat_s = heartbeat_s
        self.label = label
        self.peak = 0.0
        self._stopev = threading.Event()

    def run(self) -> None:
        # A periodic elapsed/RSS heartbeat so a long, otherwise-opaque
        # dedupe_to_parquet() call still emits a liveness signal to the CI log
        # (the whole call is one Python invocation with no per-stage callback).
        t0 = time.perf_counter()
        next_beat = self.heartbeat_s
        while not self._stopev.is_set():
            cur = _vmrss_mb()
            self.peak = max(self.peak, cur)
            elapsed = time.perf_counter() - t0
            if elapsed >= next_beat:
                print(
                    f"[fs-ooc-bench {self.label}] +{elapsed:.0f}s "
                    f"rss={cur:.0f}MB peak={self.peak:.0f}MB",
                    flush=True,
                )
                next_beat += self.heartbeat_s
            time.sleep(self.interval)

    def halt(self) -> None:
        self._stopev.set()
        self.join(timeout=1)


# Stable orthogonal blocking anchors for the `person` fixture. These are the
# columns whose value duplicates SHARE (dob/postcode are only ~5% null on dupe
# rows and are NEVER typo-offset; contrast first_name/surname which are corrupted
# 40%/50% of the time). A compound (postcode, dob) block is scale-INVARIANTLY
# small (~1.25 rows/block on the sample -- density independent of N, since both
# fields' cardinality is fixed, not row-count-scaled) yet co-blocks ~88% of true
# pairs; a second (surname-soundex, dob-year) pass lifts that to ~94%. See the
# EDGE-BEARING rationale below.
_PERSON_STABLE_PASSES = [
    # (field, transforms) tuples per pass.
    [("postcode", ["strip"]), ("dob", ["strip"])],
    [("surname", ["lowercase", "soundex"]), ("dob", ["lowercase", "substring:0:4"])],
]


def _stable_person_blocking():
    """An explicit multi_pass over the person fixture's STABLE anchors.

    The bench measures the out-of-core MECHANISM (does spill/bucketed score real
    edges under bounded memory at 50M?), so it needs blocking that is BOTH
    bounded at scale AND edge-bearing. Auto-config's own blocking is neither at
    50M: the fixture carries no stable shared identifier (email/phone/id -- only a
    per-ROW-unique ``record_id``, a perfect surrogate the pair-gate correctly
    refuses as a reducer), so the candidate-pair budget falls back to compounding
    coarse passes with corruption-prone name INITIALS (`surname:0:1`,
    `first_name:0:5`). Those split the 40%/50%-typo'd duplicate names, collapsing
    co-block recall to ~0.49 -- half the true pairs never become candidates, and
    the surviving name-compound blocks are still large. The bench then measured
    little-to-no real scoring/clustering work (the 0-edge artifact this replaces).

    Blocking on the anchors duplicates actually share -- (postcode, dob), plus a
    fuzzy-tolerant (surname-soundex, dob-year) pass -- yields ~1.25-rows/block
    bounded blocks at ANY scale with ~0.94 co-block recall, so score->cluster->
    output run over a realistic, bounded edge load. Mirrors the repo's
    `explicit-personlike` precedent (CLAUDE.md): keep auto-config's representative
    probabilistic MATCHKEY, override only the (scale-degenerate) blocking.
    """
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    passes = [
        BlockingKeyConfig(
            fields=[f for f, _ in p],
            field_transforms={f: list(t) for f, t in p},
        )
        for p in _PERSON_STABLE_PASSES
    ]
    return BlockingConfig(strategy="multi_pass", passes=passes, auto_select=False)


def _fs_config_from_sample(
    fixture: Path, n_rows_full: int, sample_rows: int = 200_000
):
    """Build the FS (probabilistic) config for the scale bench.

    Auto-config on a bounded head sample supplies the representative probabilistic
    MATCHKEY (comparison fields + scorers the gm_probabilistic lane would pick;
    auto-config itself only ever samples, so this is cheap and faithful). But the
    blocking it emits at scale is REPLACED with an explicit stable-anchor
    multi_pass -- see ``_stable_person_blocking`` for why auto-config's own
    scale-pruned blocking is edge-starved on this fixture (no stable shared
    identifier => corruption-prone name-initial compounding => ~0.49 co-block
    recall). ``n_rows_full`` is still threaded so the matchkey build sees the true
    population (EM sample cap, field admission), matching the in-memory lane."""
    import pyarrow.parquet as pq
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    pf = pq.ParquetFile(str(fixture))
    batch = next(pf.iter_batches(batch_size=sample_rows))
    import pyarrow as pa

    sample = pa.Table.from_batches([batch])
    cfg = auto_configure_probabilistic_df(sample, n_rows_full=n_rows_full)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) == "weighted":
            mk.rerank = False
    # Override the scale-degenerate blocking with the stable edge-bearing anchors.
    cfg = cfg.model_copy(update={"blocking": _stable_person_blocking()})
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument(
        "--mode",
        choices=["streaming", "sequential", "spill", "bucketed", "in_memory"],
        required=True,
        help=(
            "streaming=DuckDB out-of-core (FS_BLOCK_SOURCE=duckdb); "
            "sequential=in-RAM Arrow/Rust batches + end-WCC (FS_BLOCK_SOURCE="
            "sequential); spill=DuckDB-FREE out-of-core -- per-pass edge shards on "
            "disk + external union-find (FS_BLOCK_SOURCE=spill), the bounded-edge "
            "path; bucketed=frame-residency out-of-core -- hash-bucket the frame to "
            "disk per pass + score bucket-by-bucket (FS_BLOCK_SOURCE=bucketed), the "
            "bounded-FRAME path (never a whole-frame scoring copy); in_memory="
            "default pipeline contrast (EXPECTED OOM at 50M)"
        ),
    )
    ap.add_argument(
        "--force-shard",
        action="store_true",
        help=(
            "spill mode only: set GOLDENMATCH_FS_SPILL_FORCE_SHARD=1 so the fused "
            "short-circuit is SKIPPED and the actual edge-shard spill mechanism "
            "runs (the bounded-edge proof). Without it, a fused-covered config "
            "clusters via the fused kernel and the shard path is never exercised."
        ),
    )
    ap.add_argument("--dupe-rate", type=float, default=0.20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workdir", type=Path, default=Path(".bench_fs_ooc"))
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--keep-fixture", action="store_true")
    args = ap.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    fixture = args.workdir / f"fs_ooc_{args.rows}.parquet"
    truth = args.workdir / f"fs_ooc_{args.rows}.truth.parquet"

    sys.path.insert(0, str(Path(__file__).resolve().parent / "bench_er_headtohead"))
    import generate_fixture

    result: dict = {
        "rows": args.rows,
        "mode": args.mode,
        "dupe_rate": args.dupe_rate,
        "completed": False,
    }

    # 1) Fixture (bounded-memory streaming generator; reused if present).
    if not fixture.exists():
        t_gen = time.perf_counter()
        generate_fixture.generate(
            rows=args.rows, dupe_rate=args.dupe_rate, out=fixture, truth=truth,
            seed=args.seed, batch=1_000_000, shape="person",
        )
        result["fixture_gen_s"] = round(time.perf_counter() - t_gen, 1)
    result["fixture_bytes"] = fixture.stat().st_size

    cfg = _fs_config_from_sample(fixture, n_rows_full=args.rows)
    _bl = getattr(cfg.blocking, "strategy", None)
    _mks = [m.type for m in cfg.get_matchkeys()]
    result["blocking_strategy"] = _bl
    result["matchkey_types"] = _mks

    sampler = Sampler(label=f"{args.mode}/{args.rows}")
    sampler.start()
    t0 = time.perf_counter()
    try:
        if args.mode in ("streaming", "sequential", "spill", "bucketed"):
            # streaming -> DuckDB out-of-core; sequential -> in-RAM Arrow/Rust
            # batches + end-WCC; spill -> DuckDB-FREE out-of-core (per-pass edge
            # shards on disk + external union-find); bucketed -> frame-residency
            # out-of-core (hash-bucket the frame to disk per pass, score
            # bucket-by-bucket). All drive dedupe_to_parquet's streaming
            # short-circuit; only the block-source env differs.
            os.environ.pop("GOLDENMATCH_FS_OUT_OF_CORE", None)
            os.environ["GOLDENMATCH_FS_BLOCK_SOURCE"] = {
                "streaming": "duckdb",
                "sequential": "sequential",
                "spill": "spill",
                "bucketed": "bucketed",
            }[args.mode]
            if args.mode == "spill" and args.force_shard:
                # Skip the fused short-circuit so the edge-shard spill mechanism
                # (pair_sink shards + external_wcc_from_shards) actually runs.
                os.environ["GOLDENMATCH_FS_SPILL_FORCE_SHARD"] = "1"
                result["force_shard"] = True
            # Per-pass scoring progress in the CI log (the heartbeat covers the
            # opaque stages; this narrates the streaming scorer itself).
            os.environ["GOLDENMATCH_FS_OOC_DEBUG"] = "1"
            from goldenmatch import dedupe_to_parquet

            out_dir = args.workdir / f"out_{args.rows}"
            res = dedupe_to_parquet(str(fixture), out_dir=str(out_dir), config=cfg)
            result["streaming_engaged"] = bool(res.get("streaming"))
            result["spill_engaged"] = bool(res.get("spill"))
            result["bucketed_engaged"] = bool(res.get("bucketed"))
            # For spill: which internal path clustered — the fused kernel
            # (bounded, no pair list) or the edge-shard spill mechanism.
            if args.mode == "spill":
                result["internal_path"] = (
                    "fused" if res.get("fused") else "edge_shard"
                )
            elif args.mode == "bucketed" and not result["bucketed_engaged"]:
                result["error"] = (
                    "bucketed route did NOT engage -- streaming ran a different "
                    "orchestrator; the frame-residency bucketing was not exercised"
                )
            result["unique_count"] = res.get("unique_count")
            result["dupes_count"] = res.get("dupes_count")
            result["golden_count"] = res.get("golden_count")
            result["pairs"] = res.get("pairs")
            if not result["streaming_engaged"]:
                result["error"] = (
                    "streaming short-circuit did NOT engage -- fell back to "
                    f"in-memory (blocking={_bl}, matchkeys={_mks}); "
                    "out-of-core path was not exercised"
                )
            elif args.mode == "spill" and not result["spill_engaged"]:
                # streaming engaged but NOT via the spill orchestrator -- a
                # silent route mismatch (e.g. fused short-circuit) that would let
                # a non-spill path masquerade as the bounded-edge proof.
                result["error"] = (
                    "spill route did NOT engage -- streaming ran a DIFFERENT "
                    "orchestrator; the DuckDB-free edge-spill path was not "
                    "exercised"
                )
        else:  # in_memory contrast
            os.environ.pop("GOLDENMATCH_FS_BLOCK_SOURCE", None)
            os.environ["GOLDENMATCH_FS_OUT_OF_CORE"] = "0"
            import pyarrow.parquet as pq
            from goldenmatch import dedupe_df

            frame = pq.read_table(str(fixture))
            r = dedupe_df(frame, config=cfg, confidence_required=False)
            result["unique_count"] = (
                r.unique.num_rows if r.unique is not None else 0
            )
            result["dupes_count"] = r.dupes.num_rows if r.dupes is not None else 0
        result["completed"] = "error" not in result
    finally:
        result["wall_s"] = round(time.perf_counter() - t0, 1)
        sampler.halt()
        result["peak_rss_sampled_mb"] = round(sampler.peak)
        result["ru_maxrss_mb"] = round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        )
        if not args.keep_fixture:
            for p in (fixture, truth):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    # Best-effort fixture teardown; a leftover temp file is
                    # non-fatal (the runner is ephemeral), never fail the bench.
                    pass

    line = json.dumps(result)
    print(line)
    if args.out_json:
        args.out_json.write_text(line + "\n")


if __name__ == "__main__":
    main()
