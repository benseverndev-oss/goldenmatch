#!/usr/bin/env python
"""Single-datapoint GoldenMatch dedupe runner for the ER head-to-head bench.

Runs ONE (engine=goldenmatch, rows=N) measurement in its own process, so all of
its memory is reclaimed by the OS on exit. Writes one atomic JSON result and exits.

Most-optimized path: bucket backend + native compiled runtime + native Arrow
block-scorer. We set GOLDENMATCH_NATIVE=1 so a missing/unbuilt native runtime
raises instead of silently falling back to pure Python — a silent fallback would
make the comparison a lie. Verified again via native_enabled() before timing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

try:
    import resource  # Unix-only; absent on Windows dev boxes (CI/bench runs on Linux)
except ImportError:  # pragma: no cover - Windows fallback path
    resource = None


def _score_histogram(ded, bins: int = 100) -> dict:
    """Histogram of the scores this run ACTUALLY produced.

    Reads the Arrow pair table out of ``__dict__`` rather than the
    ``DedupeResult.scored_pairs`` property. That property calls
    ``scored_pairs_from_table``, which materialises a ``list[tuple]`` at a
    MEASURED ~168 B/pair; the 1M-row person shape scores ~1.5M pairs, so
    reading it here would add ~250 MB resident to a run whose ``peak_rss_mb``
    this same file reports two lines later. Telemetry that moves the number it
    is printed next to is worse than no telemetry.

    **This is the RETAINED-pair distribution, truncated at the cut.** It is not
    the distribution the calibrator sees and it CANNOT answer whether a dataset
    is separable. `DedupeResult.scored_pairs` holds the pairs the scorer
    emitted, which are the ones that already passed; the first version of this
    docstring claimed `largest_gap` measured separability, and the first run
    disproved it -- `gm_probabilistic` on biblio carries `link_threshold=0.85`
    and a minimum retained score of 0.9917, so all 22,767 pairs land in one
    0.01-wide bin. A gap detector fed a truncated distribution reports the
    truncation, not the structure.

    Separability is answered by `dump_fs_score_histograms.py`, which spies on
    `_posterior_split` to capture the untruncated training-pair scores AND
    splits them by ground truth. Use that; this is not a substitute.

    What this DOES show is where the retained mass sits relative to the cut,
    which is how the posterior scale's saturation became visible: everything
    accepted piles into [0.9917, 1.0] with nothing between it and the nominal
    0.85 threshold.
    """
    try:
        import numpy as np

        tbl = ded.__dict__.get("_scored_pairs_table")
        if tbl is not None and getattr(tbl, "num_rows", 0):
            scores = tbl.column("score").to_numpy(zero_copy_only=False)
        else:
            raw = ded.__dict__.get("_scored_pairs")
            if type(raw) is not list or not raw:
                return {"available": False, "reason": "no scored pairs retained"}
            scores = np.fromiter((s for _, _, s in raw), np.float64, len(raw))
        if scores.size == 0:
            return {"available": False, "reason": "empty"}

        counts, edges = np.histogram(scores, bins=bins, range=(0.0, 1.0))
        # Longest run of empty bins strictly INSIDE the observed range. Bins
        # below the minimum or above the maximum are empty for a trivial
        # reason and would report a gap that no threshold could ever sit in.
        lo_bin = int(np.searchsorted(edges, scores.min(), "right") - 1)
        hi_bin = int(np.searchsorted(edges, scores.max(), "right") - 1)
        best_len = best_start = 0
        run_len = run_start = 0
        for i in range(max(lo_bin, 0), min(hi_bin + 1, len(counts))):
            if counts[i] == 0:
                if run_len == 0:
                    run_start = i
                run_len += 1
                if run_len > best_len:
                    best_len, best_start = run_len, run_start
            else:
                run_len = 0
        return {
            "available": True,
            "n_pairs": int(scores.size),
            "min": round(float(scores.min()), 6),
            "max": round(float(scores.max()), 6),
            "mean": round(float(scores.mean()), 6),
            "quantiles": {
                q: round(float(np.quantile(scores, q / 100)), 6)
                for q in (1, 5, 25, 50, 75, 95, 99)
            },
            "bin_edges": [round(float(e), 4) for e in edges],
            "counts": [int(c) for c in counts],
            "largest_gap": {
                "bins": int(best_len),
                "lo": round(float(edges[best_start]), 4) if best_len else None,
                "hi": round(float(edges[best_start + best_len]), 4) if best_len else None,
                "width": round(float(best_len / bins), 4),
            },
        }
    except Exception as e:  # noqa: BLE001 - diagnostics must never fail a lane
        return {"available": False, "error": f"{type(e).__name__}: {e}"}


def _config_telemetry(cfg) -> dict:
    """The RESOLVED thresholds and blocking passes the run actually used.

    `link_threshold` and `blocking` were already in the emitted JSON as literal
    `null` for every lane at both scales -- present enough to look recorded,
    empty enough to be useless. Auto-config decides both, so without them a
    reader cannot tell an over-merge caused by a low cut from one caused by
    loose blocking.
    """
    out: dict = {"matchkeys": [], "blocking_passes": None}
    try:
        for mk in cfg.get_matchkeys():
            out["matchkeys"].append({
                "name": getattr(mk, "name", None),
                "type": getattr(mk, "type", None),
                "link_threshold": getattr(mk, "link_threshold", None),
                "review_threshold": getattr(mk, "review_threshold", None),
                "fields": [getattr(f, "field", None) for f in getattr(mk, "fields", [])],
                "scorers": [getattr(f, "scorer", None) for f in getattr(mk, "fields", [])],
            })
    except Exception as e:  # noqa: BLE001
        out["matchkeys_error"] = f"{type(e).__name__}: {e}"
    try:
        # `BlockingConfig.keys`, NOT `.passes`. The first version of this read
        # `.passes` and got `[]` for every lane at both scales -- a plausible
        # empty list rather than an error, which is the worst way for telemetry
        # to be wrong. `.passes` is kept as a fallback only because some callers
        # construct with that alias.
        blocking = getattr(cfg, "blocking", None)
        keys = getattr(blocking, "keys", None)
        if keys is None:
            keys = getattr(blocking, "passes", None)
        if keys is None:
            out["blocking_error"] = (
                "resolved config exposed neither blocking.keys nor .passes"
            )
        else:
            out["blocking_passes"] = [
                list(getattr(k, "fields", []) or []) for k in keys
            ]
            out["blocking_strategy"] = getattr(blocking, "strategy", None)
            out["blocking_max_block_size"] = getattr(blocking, "max_block_size", None)
    except Exception as e:  # noqa: BLE001
        out["blocking_error"] = f"{type(e).__name__}: {e}"
    return out


def _load_shapes_module():
    """Import the sibling shapes.py whether run as a script or from elsewhere."""
    try:
        import shapes as _shapes  # type: ignore
        return _shapes
    except ImportError:
        pass
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import shapes as _shapes  # type: ignore
        return _shapes
    except ImportError:
        sh_path = here / "shapes.py"
        spec = importlib.util.spec_from_file_location("shapes", sh_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

# Must be set BEFORE importing goldenmatch so the native loader + planner see them.
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")  # clean, reproducible CI runs
os.environ.setdefault("GOLDENMATCH_PLANNER_BUCKET", "1")  # prefer bucket scorer
# GOLDENMATCH_NATIVE is set from --require-native below, before the heavy imports.


def _peak_rss_mb() -> float | None:
    # Linux ru_maxrss is in KiB; this is the process high-water mark (load + dedupe).
    if resource is None:  # Windows dev box: no rusage, perf RSS only meaningful on CI.
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


def _enable_info_logging() -> None:
    """Let the library's INFO diagnostics reach the run log.

    Nothing configures logging here, so Python falls back to `lastResort`, which
    emits at WARNING -- every `logger.info` in the library produced NOTHING. That
    silently defeated the decisions this bench exists to explain: the FS
    link-threshold refit logs which of its three guards declined a candidate, and
    on person@1M that line was the answer to why the cut stayed at the default
    while precision sat at 0.263 with 673,277 false positives.

    A bench is exactly where verbosity is cheap, so INFO is the right floor. This
    is a HARNESS change, not a library one -- production keeps its own logging
    policy.

    Not a substitute for recording the decision as DATA in the result. A log line
    is lossy, order-dependent and easy to lose to a level change -- which is what
    happened here. This makes the next run answerable; persisting the refit
    decision alongside `fs_link_thresholds` makes every run answerable.
    """
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    _enable_info_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-out", type=Path, default=None,
                    help="write {record_id, pred_cluster_id} parquet for accuracy eval")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument(
        "--force-link-threshold", type=float, default=None,
        help=(
            "Force the FS link cut WITHOUT the --fs-basic-scorers scorer "
            "rewrite. `gm_probabilistic` couples the two, so its delta against "
            "`gm_probabilistic_shipped` is a three-way confound (cut + scorers "
            "+ calibration) and cannot say which term dominates. This isolates "
            "the cut."
        ),
    )
    ap.add_argument("--mode", choices=["hand_built", "zeroconfig", "zeroconfig_replay", "probabilistic"],
                    default="hand_built",
                    help="hand_built = explicit bucket+native config (default); "
                         "zeroconfig = auto_configure_df controller; "
                         "probabilistic = Fellegi-Sunter auto-config")
    ap.add_argument("--shape", choices=["person", "biblio", "product"], default="person",
                    help="fixture shape; selects the hand_built config from shapes.py")
    ap.add_argument("--require-native", action="store_true", default=True)
    ap.add_argument("--allow-pure-python", dest="require_native", action="store_false")
    ap.add_argument("--fs-basic-scorers", action="store_true", default=False,
                    help="probabilistic mode only: rewrite any FS field scorer NOT in "
                         "the native FS kernel set to jaro_winkler, so both FS lanes "
                         "engage the Rust kernel and match Splink's JaroWinkler model")
    ap.add_argument("--measure-refused", action="store_true", default=False,
                    help="zeroconfig mode only: when the controller REFUSES a RED "
                         "config (>=100K identifier-poor input), MEASURE the committed "
                         "config's F1 by force-running it with allow_red_config=True "
                         "(the QIS-gate idiom) and flag refused=true, instead of "
                         "recording status=refused with no prediction. Required by the "
                         "cost-aware OFF-vs-ON gate, whose person shape refuses by "
                         "design (the #2021 case). Default OFF preserves the head-to-"
                         "head matrix's refuse-and-record semantics.")
    args = ap.parse_args()

    os.environ["GOLDENMATCH_NATIVE"] = "1" if args.require_native else "auto"

    result: dict = {
        "engine": "goldenmatch",
        "backend": "bucket+native+arrow",
        "rows_requested": args.rows,
        "status": "error",
        "threshold": args.threshold,
        "mode": args.mode,
        "shape": args.shape,
    }
    t_start = time.perf_counter()
    try:
        import pyarrow.parquet as pq
        from goldenmatch.core._native_loader import native_enabled, native_module
        from goldenmatch.core.bench import bench_capture

        shapes = _load_shapes_module()

        try:
            from goldenmatch import dedupe_df
        except ImportError:  # older layouts expose this on _api
            from goldenmatch._api import dedupe_df

        native_loaded = native_module() is not None
        result["native_loaded"] = native_loaded
        result["native_block_scoring"] = bool(native_enabled("block_scoring"))
        # FS-native telemetry: was the probabilistic Rust kernel REQUESTED
        # (GOLDENMATCH_FS_NATIVE) and is the symbol actually present? Lets the
        # bake-off artifact prove a `gm_prob_native` row really ran the kernel
        # and didn't silently fall back to numpy (probabilistic mode never
        # refuses on a missing kernel, unlike hand_built).
        result["fs_native_requested"] = (
            os.environ.get("GOLDENMATCH_FS_NATIVE", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        result["fs_native_symbol_present"] = bool(
            native_loaded and hasattr(native_module(), "score_block_pairs_fs")
        )
        # The native gate only applies to the hand_built optimized-path claim. The
        # autoconfig modes let the controller pick the backend, so a missing native
        # runtime is recorded for info but does NOT refuse the run.
        if (
            args.mode == "hand_built"
            and args.require_native
            and not (native_loaded and native_enabled("block_scoring"))
        ):
            raise RuntimeError(
                "Native Arrow block-scorer is NOT active; refusing to report a "
                "pure-Python number as the optimized backend. Build it with "
                "`python scripts/build_native.py` or install goldenmatch[native]."
            )

        t0 = time.perf_counter()
        df = pq.read_table(args.input)
        result["rows_loaded"] = df.num_rows
        load_wall = time.perf_counter() - t0

        if args.mode == "hand_built":
            # GoldenMatch's MOST-OPTIMIZED path: explicit bucket+native config (not
            # the zero-config controller, which adds 30s+ overhead and can commit a
            # RED config on off-distribution data). Mirrors Splink's hand-built spec
            # — compound blocking + native Jaro-Winkler scoring — for a fair
            # head-to-head. NOTE: the bucket backend does SINGLE-KEY blocking (one
            # eager bucket pass — it ignores multi_pass `passes`); that's how it
            # stays fast at scale. So we give it its best single key. Splink, by
            # contrast, unions multiple blocking rules — a real engine difference the
            # benchmark surfaces rather than hides. The per-shape config is the single
            # source of truth in shapes.py (person blocks on stable postcode; biblio
            # on the composite (venue, year) key).
            config = shapes.SHAPES[args.shape].gm_hand_built(args.threshold)

            t0 = time.perf_counter()
            with bench_capture() as bench:
                ded = dedupe_df(df, config=config)
            dedupe_wall = time.perf_counter() - t0
        elif args.mode == "zeroconfig_replay":
            # CONFIG vs PATH, isolated.
            #
            # gm_zeroconfig and gm_probabilistic_shipped commit configs that are
            # identical on every field the telemetry can see -- same 8 blocking
            # passes, same matchkeys/scorers, and `backend` (the one field that
            # differed) is inert because _use_bucket_scorer already returns True
            # with backend=None. Both even run the same bucket_* stages. Yet
            # zero-config retains 9,250 pairs to the other's 142,933 and scores
            # pairwise F1 0.5536 vs 0.9970, with bucket_score 0.38s vs 3.67s --
            # roughly 10x less scoring work.
            #
            # Blocking is deterministic given (config, frame), so an identical
            # config that behaves differently means the FRAME or the PATH
            # differs, not the plan. This lane takes zero-config's OWN committed
            # config and runs it through the EXPLICIT path, exactly as the
            # probabilistic lane does:
            #
            #   ~0.997 -> the config is fine; the zero-config PATH is the bug
            #   ~0.554 -> the config carries something the telemetry cannot see
            from goldenmatch.core.autoconfig import auto_configure_df

            t0 = time.perf_counter()
            cfg = auto_configure_df(df, confidence_required=False)
            for mk in cfg.get_matchkeys():
                if getattr(mk, "type", None) == "weighted":
                    mk.rerank = False
            result["config_resolved"] = _config_telemetry(cfg)
            with bench_capture() as bench:
                ded = dedupe_df(df, config=cfg)
            dedupe_wall = time.perf_counter() - t0
        elif args.mode == "zeroconfig":
            # ControllerNotConfidentError lives in autoconfig_controller (NOT
            # autoconfig); it only fires at df.height >= 100K on a RED commit, so it
            # won't trigger on the panel datasets — it's a defensive guard.
            from goldenmatch.core.autoconfig_controller import (
                ControllerNotConfidentError,
            )

            t0 = time.perf_counter()
            try:
                with bench_capture() as bench:
                    ded = dedupe_df(df)
                result["refused"] = False
            except ControllerNotConfidentError as e:
                if not args.measure_refused:
                    dedupe_wall = time.perf_counter() - t0
                    result.update(status="refused", error=str(e),
                                  dedupe_wall_seconds=round(dedupe_wall, 2))
                    result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
                    result["peak_rss_mb"] = _peak_rss_mb()
                    _atomic_write(args.out, result)
                    print(
                        f"[goldenmatch] rows={args.rows:,} mode={args.mode} "
                        f"status=refused error={e}"
                    )
                    return
                # --measure-refused: a RED refusal is EXPECTED on identifier-poor
                # inputs (the #2021 person case the cost-aware flag targets), and the
                # OFF-vs-ON gate needs the committed config's ACTUAL F1 to compare.
                # Re-config + force-run the SAME committed config with
                # allow_red_config=True (+ _skip_finalize=True to skip the full-df
                # verification profile) -- the QIS-gate measure_rungs idiom -- and
                # flag refused=true for context. Pass/fail stays on the measured F1,
                # not the refuse verdict (RED is a confidence flag, not a wrong
                # answer). The force-run's bench_capture is what the pred-out +
                # metrics below use, so they reflect the config actually scored.
                from goldenmatch import auto_configure_df
                result["refused"] = True
                cfg = auto_configure_df(
                    df, confidence_required=False, allow_red_config=True,
                    _skip_finalize=True)
                with bench_capture() as bench:
                    ded = dedupe_df(df, config=cfg)
                print(
                    f"[goldenmatch] rows={args.rows:,} mode={args.mode} "
                    f"status=refused-measured (allow_red_config force-run) error={e}"
                )
            dedupe_wall = time.perf_counter() - t0
        else:  # probabilistic
            from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

            cfg = auto_configure_probabilistic_df(df)
            # Cut where the bench SAYS it cuts. `--threshold` was parsed and
            # written into the result JSON but never applied on this path, so
            # every FS datapoint was produced at GoldenMatch's internal
            # fallback link cut (a fixed 0.99 under posterior calibration,
            # `source: "fallback"` -- nothing about the dataset chose it) while
            # the file claimed 0.85 and `run_splink.py` really did cluster at
            # 0.85 via `cluster_pairwise_predictions_at_threshold`.
            #
            # So the two engines were compared ~14 points apart on the same
            # probability scale, which is the opposite of what this lane's
            # `GOLDENMATCH_FS_CALIBRATED=posterior` exists to guarantee.
            # Measured on a 20K person fixture, the cut alone is worth
            # F1 0.5406 (at 0.95) -> 0.8772 (at 0.85) with precision staying
            # 1.0000 throughout -- see `diagnose_fs_recall.py`.
            #
            # Tied to `--fs-basic-scorers` because both adjustments serve the
            # SAME purpose -- making this lane comparable to Splink -- and a
            # lane that is not being compared to Splink should not inherit
            # either. `gm_probabilistic_shipped` runs without the flag and so
            # keeps GM's own threshold decision, which is the point of having
            # it: a control that shows what GoldenMatch actually does.
            if args.fs_basic_scorers:
                for mk in cfg.get_matchkeys():
                    mk.link_threshold = args.threshold
            # The cut alone, nothing else touched. person@1M's shipped cut is
            # 0.50 while its MINIMUM score is 0.60 (run 32078393523,
            # `cut_is_inert: true`), so it admits every scored pair and the
            # 0.2627 precision follows from that plus transitive closure. The
            # recorded sweep puts the knee at 0.80 -- largest component
            # 618 -> 3, expelled 0.1002 -- and going higher buys no further
            # reduction at 2-6x the expelled cost.
            if args.force_link_threshold is not None:
                for mk in cfg.get_matchkeys():
                    mk.link_threshold = args.force_link_threshold
            # Force rerank off so a 3+ field weighted matchkey can't pull a
            # cross-encoder model down from HuggingFace at dedupe time.
            for mk in cfg.get_matchkeys():
                if getattr(mk, "type", None) == "weighted":
                    mk.rerank = False
            # Optionally force any NON-BASIC scorer autoconfig picked -- the
            # reference-data name scorers (given_name_aliased_jw /
            # name_freq_weighted_jw), ensemble, embedding -- to jaro_winkler. This
            # makes BOTH FS lanes run the SAME basic JaroWinkler model: native-
            # eligible via the base kernel ids AND comparable to Splink's own
            # JaroWinkler FS. Runs BEFORE the eligibility telemetry below so the
            # counts reflect the config actually scored.
            #
            # "Basic" is the FIXED base set {jaro_winkler, levenshtein, token_sort,
            # exact} -- the four string scorers the native FS kernel has always
            # scored unconditionally and that mirror Splink's comparison functions.
            # We key on this fixed set, NOT on `_NATIVE_FS_SCORER_IDS`: that set has
            # since grown to include the name scorers (ids 4/5, behind the optional
            # FS_SUPPORTS_NAME_SCORERS wheel capability + a loaded refdata pack), so
            # keying on it would leave the specialized name scorers in place --
            # silently breaking both the "basic scorers" contract and the Splink
            # JaroWinkler comparability the flag exists to guarantee.
            _FS_BASIC_SCORERS = {"jaro_winkler", "levenshtein", "token_sort", "exact"}
            rewritten: list = []
            if args.fs_basic_scorers:
                for mk in cfg.get_matchkeys():
                    for f in getattr(mk, "fields", None) or []:
                        sc = getattr(f, "scorer", None)
                        if sc is not None and sc not in _FS_BASIC_SCORERS:
                            rewritten.append((getattr(f, "field", None), sc))
                            f.scorer = "jaro_winkler"
            result["fs_basic_scorers_rewritten"] = rewritten
            # ALWAYS recorded, present or absent, so a reader never has to infer
            # from a missing key whether this number is comparable-to-Splink or
            # as-shipped. Reading a handicapped F1 as "GoldenMatch's accuracy"
            # is a real failure mode -- it cost this repo a five-session detour
            # on a 20K person fixture where the handicapped config scores
            # F1 0.0000 and the shipped default scores 0.9964.
            result["handicaps"] = {
                "fs_basic_scorers": bool(args.fs_basic_scorers),
                "forced_link_threshold": (
                    args.threshold if args.fs_basic_scorers else None
                ),
                "fs_calibrated": os.environ.get("GOLDENMATCH_FS_CALIBRATED"),
                "comparable_to_splink": bool(args.fs_basic_scorers),
            }
            # FS-native per-matchkey eligibility telemetry (spec section 8): count
            # how many resolved matchkeys the native FS kernel could score. Under
            # the numpy lane (GOLDENMATCH_FS_NATIVE=0) _fs_native_enabled()
            # short-circuits, so _fs_native_eligible is False for every mk -> 0.
            # Guarded so a future internal rename degrades to None, not a crash.
            try:
                from goldenmatch.core.probabilistic import (
                    _fs_native_eligible,
                    _fs_native_enabled,
                )

                mks = cfg.get_matchkeys()
                result["fs_matchkeys_total"] = len(mks)
                result["fs_native_eligible_matchkeys"] = sum(
                    1 for mk in mks if _fs_native_eligible(mk)
                )
                result["fs_native_gate"] = bool(_fs_native_enabled())
            except (ImportError, AttributeError):
                result["fs_matchkeys_total"] = None
                result["fs_native_eligible_matchkeys"] = None
                result["fs_native_gate"] = None
            t0 = time.perf_counter()
            with bench_capture() as bench:
                ded = dedupe_df(df, config=cfg)
            dedupe_wall = time.perf_counter() - t0

        # Per-record cluster assignment for accuracy eval. clusters is
        # {cid: {"members": [__row_id__...]}} over ALL records.
        if args.pred_out is not None:
            import numpy as np
            import pyarrow as pa
            import pyarrow.parquet as pq

            clusters = getattr(ded, "clusters", None) or {}
            if args.mode == "hand_built":
                # The optimized-path benchmark's truth join is int64 (orchestrate.py
                # back-compat): the fixture's record_id IS the input row index, and
                # GoldenMatch preserves it as __row_id__, so member row-ids are
                # record-ids directly.
                rids, cids = [], []
                for cid, c in clusters.items():
                    members = c["members"] if isinstance(c, dict) else c.members
                    rids.extend(members)
                    cids.extend([cid] * len(members))
                pq.write_table(
                    pa.table(
                        {
                            "record_id": pa.array(np.asarray(rids, dtype=np.int64)),
                            "pred_cluster_id": pa.array(np.asarray(cids, dtype=np.int64)),
                        }
                    ),
                    args.pred_out,
                    compression="zstd",
                )
            else:
                # Autoconfig modes: remap internal __row_id__ back to the input df's
                # REAL record_id as a STRING column (mirrors run_panel.py:83-108).
                # The real benchmark datasets carry STRING record_ids (historical_50k
                # Q-ids, dblp_acm 'dblp:123', febrl3 'rec-123-org').
                rid = df.column("record_id").to_pylist()
                rids, cids = [], []
                for cid, c in clusters.items():
                    members = c["members"] if isinstance(c, dict) else c.members
                    for m in members:
                        rids.append(str(rid[m]))
                        cids.append(cid)
                pq.write_table(
                    pa.table(
                        {
                            "record_id": pa.array(rids, pa.string()),
                            "pred_cluster_id": pa.array(np.asarray(cids, dtype=np.int64)),
                        }
                    ),
                    args.pred_out,
                    compression="zstd",
                )

        bench_blob = bench.to_dict()
        metrics = bench_blob.get("metrics", {}) if isinstance(bench_blob, dict) else {}

        result.update(
            status="ok",
            load_wall_seconds=round(load_wall, 2),
            dedupe_wall_seconds=round(dedupe_wall, 2),
            scored_pairs=metrics.get("scored_pair_count"),
            block_count=metrics.get("block_count_scored") or metrics.get("block_count"),
            # cluster_count = total resolved entities incl. singletons, to match
            # Splink's `count(distinct cluster_id)`. multi-member tracked separately.
            cluster_count=metrics.get("cluster_count"),
            multi_member_clusters=metrics.get("multi_member_cluster_count"),
            duplicate_rows_found=getattr(getattr(ded, "dupes", None), "num_rows", None),
            unique_records=getattr(getattr(ded, "unique", None), "num_rows", None),
            bench=bench_blob,
        )
        # AFTER the result.update above, so a diagnostics bug cannot cost the
        # measurement -- the numbers are already recorded by the time these run.
        result["score_histogram"] = _score_histogram(ded)
        # `ded.config` deliberately, NOT the `cfg` handed to dedupe_df: on the
        # auto-config modes the resolved config is what auto-config committed,
        # and the pre-run object can carry thresholds it overwrote.
        # The cutoff the run ACTUALLY applied, its provenance, and what the
        # threshold refit decided about it. `config_resolved` records only what
        # was CONFIGURED -- on this lane `link_threshold` is null, which says
        # nothing about where the cut landed or why. That gap is what made the
        # person@1M over-merge unanswerable from the artifact: three separate
        # changes were aimed at the wrong branch because no run recorded which
        # decision was in force.
        _stats = getattr(ded, "stats", None) or {}
        result["fs_link_thresholds"] = _stats.get("fs_link_thresholds")

        _resolved = getattr(ded, "config", None)
        result["config_resolved"] = (
            _config_telemetry(_resolved) if _resolved is not None
            else {"available": False, "reason": "DedupeResult carried no config"}
        )
    except MemoryError as e:
        result.update(status="OOM", error=f"{type(e).__name__}: {e}")
    except BaseException as e:  # noqa: BLE001 - record any failure, including SystemError
        result.update(status="error", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        result["total_wall_seconds"] = round(time.perf_counter() - t_start, 2)
        result["peak_rss_mb"] = _peak_rss_mb()
        _atomic_write(args.out, result)
        print(
            f"[goldenmatch] rows={args.rows:,} mode={args.mode} "
            f"status={result['status']} "
            f"dedupe={result.get('dedupe_wall_seconds')}s "
            f"peak_rss={result['peak_rss_mb']}MB pairs={result.get('scored_pairs')}"
        )


if __name__ == "__main__":
    main()
