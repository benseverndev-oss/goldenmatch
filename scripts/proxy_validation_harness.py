#!/usr/bin/env python3
"""Do auto-config's commit proxies predict quality? Measure, don't argue.

`RunHistory.pick_committed` ranks candidate configs on proxies -- `health()`,
`-zero_label.overall_confidence`, `-mass_separation`. Every one of them was
designed from first principles and validated by "did the quality gate stay
green", which only asks whether the COMMITTED outcome regressed. It has never
been asked whether the proxies RANK candidates correctly.

There is good reason to doubt they do:

  * `mass_above_threshold` is 1.0 whenever anything matched (#2672), and ~12
    rules gate on hardcoded cuts of it.
  * `overall_confidence` is therefore `min(blend, 0.2)` on essentially every
    run that matched anything, because the everything-matches guard fires at
    `mass_above_threshold >= 0.9`. A seven-term weighted blend that collapses
    to a constant cannot rank anything.
  * `-sep` rises when the threshold falls, so "separates better" and
    "over-merges" are the same movement. Measured: committing the candidate
    that won on `-sep` took Abt-Buy dedupe F1 0.0881 -> 0.0746 (#2748).

The labels to settle this already exist -- the repo carries ~6 scored benchmark
lanes -- but they are used only as a pass/fail gate, never as a signal for
calibrating the proxies. This harness closes that loop: it dumps every
candidate the controller considered, with its full profile AND its TRUE F1, so
each proxy's rank correlation against ground truth becomes a number.

Labels at design time, not runtime: zero-config stays the product promise. This
calibrates the zero-label proxies offline.

USAGE
    python scripts/proxy_validation_harness.py --out candidates.jsonl
    python scripts/proxy_validation_harness.py --analyze candidates.jsonl

HOW IT STAYS HONEST
Every lane is scored through the SAME shipped helper the benchmark uses
(`run_two_source_dedupe_zeroconfig`, `evaluate_ncvr`, ...), with only the
injected dedupe/match callable swapped to pin a candidate's config. And the
harness self-validates: re-scoring the COMMITTED candidate must reproduce the
zero-config run's F1. If it does not, config-pinning is not faithful and every
number here is void -- so that check is a hard failure, not a warning.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_DEFAULT_DATASETS_DIR = (
    _SCRIPTS_DIR.parent
    / "packages"
    / "python"
    / "goldenmatch"
    / "tests"
    / "benchmarks"
    / "datasets"
)

#: Re-scoring the committed candidate must land within this of the zero-config
#: run's F1. Not zero: the controller samples, so a re-run can differ slightly.
#: Anything larger means the pinned config is not the config that ran.
_FIDELITY_TOLERANCE = 0.02


def _info(msg: str) -> None:
    print(f"[proxy-harness] {msg}", flush=True)


# ── candidate scoring ────────────────────────────────────────────────────────


@dataclasses.dataclass
class Lane:
    """One scored benchmark lane.

    `evaluate` takes the dedupe/match callable the shipped helper expects and
    returns an object carrying `.f1` / `.precision` / `.recall`. Pinning a
    candidate config means passing a different callable -- nothing else about
    the scoring path changes.
    """

    name: str
    kind: str  # "dedupe" | "linkage"
    evaluate: Callable[[Callable], Any]


def _flatten_profile(profile: Any) -> dict[str, Any]:
    """Every scalar on the ComplexityProfile, as `subprofile.field`.

    Deliberately exhaustive rather than a curated list: the point is to find
    which fields predict quality, and hand-picking them would presuppose the
    answer.
    """
    out: dict[str, Any] = {}
    if profile is None:
        return out
    for sub in dataclasses.fields(profile):
        node = getattr(profile, sub.name, None)
        if node is None or not dataclasses.is_dataclass(node):
            if isinstance(node, (int, float, bool)) and not isinstance(node, bool):
                out[sub.name] = node
            continue
        for f in dataclasses.fields(node):
            v = getattr(node, f.name, None)
            if isinstance(v, bool):
                out[f"{sub.name}.{f.name}"] = int(v)
            elif isinstance(v, (int, float)):
                out[f"{sub.name}.{f.name}"] = v
    # Derived quantities the ranking actually uses, so they are directly testable.
    sp = getattr(profile, "scoring", None)
    if sp is not None:
        out["derived.mass_separation"] = sp.mass_above_threshold - sp.mass_in_borderline
    try:
        out["derived.health_rank"] = {"green": 0, "yellow": 1, "red": 2}[profile.health().value]
    except Exception:
        pass
    return out


def _score_candidate(lane: Lane, config: Any, kind: str) -> dict[str, Any] | None:
    """Score one candidate config through the lane's shipped evaluator."""
    from goldenmatch import dedupe_df, match_df

    if kind == "dedupe":
        fn = functools.partial(dedupe_df, config=config, allow_red_config=True)
    else:
        fn = functools.partial(match_df, config=config, allow_red_config=True)
    start = time.time()
    try:
        res = lane.evaluate(fn)
    except Exception as exc:  # a candidate config can be genuinely unrunnable
        return {
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "elapsed_seconds": round(time.time() - start, 2),
        }
    if res is None:
        return None
    return {
        "true_f1": round(float(res.f1), 6),
        "true_precision": round(float(res.precision), 6),
        "true_recall": round(float(res.recall), 6),
        "elapsed_seconds": round(time.time() - start, 2),
    }


#: Threshold grid for generated candidates. Threshold is the dominant lever --
#: it moves precision/recall directly -- so sweeping it is the cheapest way to
#: manufacture the quality SPREAD the controller's own candidates lack.
_THRESHOLD_GRID = (0.45, 0.55, 0.65, 0.75, 0.85, 0.95)


def _config_variants(base: Any) -> list[tuple[str, Any]]:
    """Perturb a real config into a grid spanning threshold x splitting.

    Starts from a config the controller actually produced for THIS dataset, so
    every variant is valid for its schema; only the levers under test move.
    """
    import copy

    from goldenmatch.config.schemas import ClusterConfig

    out: list[tuple[str, Any]] = []
    for thr in _THRESHOLD_GRID:
        for split in (False, True):
            cfg = copy.deepcopy(base)
            mks = getattr(cfg, "matchkeys", None) or []
            for mk in mks:
                if getattr(mk, "threshold", None) is not None:
                    mk.threshold = thr
            if not mks:
                continue
            cfg.cluster = ClusterConfig(
                split_weak_bridges=split,
                weak_bridge_margin=0.0 if split else None,
            )
            out.append((f"thr={thr:.2f} split={int(split)}", cfg))
    return out


def _assemble_profile(emitter: Any, config: Any) -> Any:
    """Rebuild a ComplexityProfile from captured emissions, as the controller does.

    IMPORTANT BASIS NOTE. `pick_committed` ranks on profiles measured from the
    controller's SAMPLE runs. These are measured on the FULL run of the pinned
    config, so they are not the same objects the commit decision sees.

    That is deliberate, and it makes this a NECESSARY-CONDITION test: if a
    signal carries no information about F1 even when measured on the very run
    being scored, it cannot carry more when estimated from a sample. A proxy
    that fails here is dead; a proxy that passes here still has to survive the
    sampling step before it can be trusted at commit time.

    `random_pair_above_threshold_rate` stays at its default -- the controller
    computes it via `_compute_recall_probe`, which needs controller context.
    Treat that one field as unmeasured here rather than as zero.
    """
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ClusterProfile,
        ComplexityProfile,
        DataProfile,
        DomainProfile,
        MatchkeyProfile,
        ScoringProfile,
    )
    from goldenmatch.core.zero_label_confidence import compute_zero_label_confidence

    profile = ComplexityProfile(
        data=emitter.data or DataProfile(),
        domain=emitter.domain or DomainProfile(),
        matchkey=emitter.matchkey or MatchkeyProfile(),
        blocking=emitter.blocking or BlockingProfile(),
        scoring=emitter.scoring or ScoringProfile(),
        cluster=emitter.cluster or ClusterProfile(),
    )
    return dataclasses.replace(
        profile,
        zero_label=compute_zero_label_confidence(profile, config),
    )


def _score_generated(lane: Lane, config: Any, kind: str) -> dict[str, Any] | None:
    """Score one generated config AND capture the profile the run emitted."""
    from goldenmatch import dedupe_df, match_df
    from goldenmatch.core.profile_emitter import profile_capture

    base = dedupe_df if kind == "dedupe" else match_df
    fn = functools.partial(base, config=config, allow_red_config=True)
    start = time.time()
    try:
        with profile_capture() as emitter:
            res = lane.evaluate(fn)
            profile = _assemble_profile(emitter, config)
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "elapsed_seconds": round(time.time() - start, 2),
        }
    if res is None:
        return None
    return {
        "true_f1": round(float(res.f1), 6),
        "true_precision": round(float(res.precision), 6),
        "true_recall": round(float(res.recall), 6),
        "elapsed_seconds": round(time.time() - start, 2),
        **_flatten_profile(profile),
    }


def run_lane_generated(lane: Lane) -> list[dict[str, Any]]:
    """Sweep a generated config grid to get the quality spread the controller
    never produces, so proxy validity is actually measurable."""
    _info(f"{lane.name}: zero-config pass (for a valid base config)")
    history, zero_score = _capture_history(lane, lane.kind)
    if history is None or zero_score is None:
        _info(f"{lane.name}: no history/score -- skipping")
        return []
    try:
        committed = history.pick_committed(
            precision_collapse_floor=0.9,
            use_zero_label_confidence=True,
        )
    except Exception:
        committed = None
    base_cfg = getattr(committed, "config", None)
    if base_cfg is None:
        base_cfg = next((e.config for e in reversed(history.entries) if e.config is not None), None)
    if base_cfg is None:
        _info(f"{lane.name}: no usable base config -- skipping")
        return []

    variants = _config_variants(base_cfg)
    _info(
        f"{lane.name}: zero-config f1={zero_score['true_f1']:.4f}; "
        f"sweeping {len(variants)} generated configs"
    )
    rows: list[dict[str, Any]] = []
    for label, cfg in variants:
        scored = _score_generated(lane, cfg, lane.kind)
        if scored is None:
            continue
        rows.append(
            {
                "lane": lane.name,
                "kind": lane.kind,
                "variant": label,
                "generated": True,
                "zero_config_f1": zero_score["true_f1"],
                **scored,
            }
        )
        f1 = scored.get("true_f1")
        _info(
            f"  {label:<22} f1={'ERR' if f1 is None else f'{f1:.4f}'} "
            f"({scored.get('elapsed_seconds')}s)"
            + (f"  {scored['error']}" if "error" in scored else "")
        )
    return rows


def _capture_history(lane: Lane, kind: str) -> tuple[Any, dict[str, Any] | None]:
    """Run the lane zero-config; return (history, zero_config_score)."""
    from goldenmatch import dedupe_df, match_df
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN

    base = dedupe_df if kind == "dedupe" else match_df
    start = time.time()
    res = lane.evaluate(base)
    elapsed = time.time() - start
    captured = _LAST_CONTROLLER_RUN.get()
    history = captured[1] if isinstance(captured, tuple) else captured
    score = None
    if res is not None:
        score = {
            "true_f1": round(float(res.f1), 6),
            "true_precision": round(float(res.precision), 6),
            "true_recall": round(float(res.recall), 6),
            "elapsed_seconds": round(elapsed, 2),
        }
    return history, score


def run_lane(lane: Lane) -> list[dict[str, Any]]:
    """Zero-config once to collect candidates, then score every candidate."""
    _info(f"{lane.name}: zero-config pass (collecting candidates)")
    history, zero_score = _capture_history(lane, lane.kind)
    if history is None or not getattr(history, "entries", None):
        _info(f"{lane.name}: no controller history -- skipping")
        return []
    if zero_score is None:
        _info(f"{lane.name}: lane returned no score -- skipping")
        return []

    try:
        committed = history.pick_committed(
            precision_collapse_floor=0.9,
            use_zero_label_confidence=True,
        )
    except Exception:
        committed = None
    committed_iter = committed.iteration if committed is not None else None

    entries = [e for e in history.entries if e.profile is not None]
    _info(
        f"{lane.name}: zero-config f1={zero_score['true_f1']:.4f}, "
        f"{len(entries)} candidates, committed=iter {committed_iter}"
    )

    rows: list[dict[str, Any]] = []
    for e in entries:
        if e.config is None:
            _info(f"  iter {e.iteration}: no config on entry -- cannot re-score")
            continue
        scored = _score_candidate(lane, e.config, lane.kind)
        if scored is None:
            continue
        rule = e.decision.rule_name if e.decision is not None else None
        row = {
            "lane": lane.name,
            "kind": lane.kind,
            "iteration": e.iteration,
            "is_v0": e.iteration < 0,
            "is_committed": e.iteration == committed_iter,
            "rule_that_produced_next": rule,
            "zero_config_f1": zero_score["true_f1"],
            **scored,
            **_flatten_profile(e.profile),
        }
        rows.append(row)
        f1 = scored.get("true_f1")
        _info(
            f"  iter {e.iteration:>3}"
            f"{' [COMMITTED]' if row['is_committed'] else '           '} "
            f"f1={'ERR' if f1 is None else f'{f1:.4f}'} "
            f"({scored.get('elapsed_seconds')}s)"
            + (f"  {scored['error']}" if "error" in scored else "")
        )

    _validate_fidelity(lane, rows, zero_score)
    return rows


def _validate_fidelity(lane: Lane, rows: list[dict], zero_score: dict) -> None:
    """The committed candidate, re-scored, must reproduce the zero-config F1.

    This is the load-bearing check. If pinning `config=` does not reproduce what
    auto-config actually ran, then every `true_f1` here belongs to some other
    configuration and the whole table is a lookalike. Raise rather than warn --
    a harness that silently measures the wrong thing is worse than none.
    """
    committed = [r for r in rows if r.get("is_committed") and "true_f1" in r]
    if not committed:
        _info(f"{lane.name}: WARNING -- committed candidate not re-scored, fidelity unverified")
        return
    got = committed[0]["true_f1"]
    want = zero_score["true_f1"]
    delta = abs(got - want)
    if delta > _FIDELITY_TOLERANCE:
        raise SystemExit(
            f"FIDELITY FAILURE on {lane.name}: re-scoring the committed config "
            f"gave f1={got:.4f} but the zero-config run gave f1={want:.4f} "
            f"(delta {delta:.4f} > {_FIDELITY_TOLERANCE}). Pinning `config=` is "
            f"not reproducing what auto-config ran, so every true_f1 in this "
            f"table describes a different configuration. Fix before trusting."
        )
    _info(
        f"{lane.name}: fidelity OK (committed re-score {got:.4f} vs "
        f"zero-config {want:.4f}, delta {delta:.4f})"
    )


# ── lanes ────────────────────────────────────────────────────────────────────


def _product_lanes(datasets_dir: Path, key: str) -> list[Lane]:
    from dqbench_adapters.leipzig_eval import (
        run_two_source_dedupe_zeroconfig,
        run_two_source_link_zeroconfig,
    )
    from run_benchmarks import _PRODUCT_SPECS

    spec = _PRODUCT_SPECS[key]
    kw = dict(
        subdir=spec["subdir"],
        file_a=spec["file_a"],
        file_b=spec["file_b"],
        gt_file=spec["gt_file"],
        gt_cols=spec["gt_cols"],
        src_a=spec["src_a"],
        src_b=spec["src_b"],
        rename=spec["rename"],
    )
    if not (datasets_dir / spec["subdir"]).exists():
        return []
    return [
        Lane(
            f"{spec['label']} (dedupe)",
            "dedupe",
            lambda fn, kw=kw: run_two_source_dedupe_zeroconfig(datasets_dir, fn, **kw),
        ),
        Lane(
            f"{spec['label']} (linkage)",
            "linkage",
            lambda fn, kw=kw: run_two_source_link_zeroconfig(datasets_dir, fn, **kw),
        ),
    ]


def _dblp_lane(datasets_dir: Path) -> list[Lane]:
    from dqbench_adapters.leipzig_eval import run_dblp_acm_zeroconfig

    if not (datasets_dir / "DBLP-ACM" / "DBLP2.csv").exists():
        return []
    return [
        Lane("DBLP-ACM (linkage)", "linkage", lambda fn: run_dblp_acm_zeroconfig(datasets_dir, fn))
    ]


def _ncvr_lane(datasets_dir: Path) -> list[Lane]:
    try:
        from dqbench_adapters.ncvr import (
            build_ncvr_df_and_gt,
            build_ncvr_synthetic_df_and_gt,
            evaluate_ncvr,
        )
    except Exception:
        return []
    loaded = build_ncvr_df_and_gt(datasets_dir / "NCVR" / "ncvoter_sample_10k.txt")
    label = "NCVR (dedupe)"
    if loaded is None:
        loaded = build_ncvr_synthetic_df_and_gt()
        label = "NCVR-synthetic (dedupe)"
    if loaded is None:
        return []
    df, gt = loaded
    return [Lane(label, "dedupe", lambda fn: evaluate_ncvr(df, gt, fn))]


def _febrl3_lane() -> list[Lane]:
    try:
        from dqbench_adapters.febrl3 import evaluate_febrl3, load_febrl3_df_and_gt
    except Exception:
        return []
    loaded = load_febrl3_df_and_gt()
    if loaded is None:
        return []
    df, gt = loaded
    return [Lane("Febrl3 (dedupe)", "dedupe", lambda fn: evaluate_febrl3(df, gt, fn))]


def build_lanes(datasets_dir: Path, only: set[str] | None) -> list[Lane]:
    lanes: list[Lane] = []
    lanes += _product_lanes(datasets_dir, "abt-buy")
    lanes += _product_lanes(datasets_dir, "amazon-google")
    lanes += _dblp_lane(datasets_dir)
    lanes += _ncvr_lane(datasets_dir)
    lanes += _febrl3_lane()
    if only:
        lanes = [ln for ln in lanes if any(o.lower() in ln.name.lower() for o in only)]
    return lanes


# ── analysis ─────────────────────────────────────────────────────────────────


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, ties averaged. No scipy dependency."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(vs: list[float]) -> list[float]:
        order = sorted(range(len(vs)), key=lambda i: vs[i])
        out = [0.0] * len(vs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None  # constant -- carries no ranking information AT ALL
    return num / (dx * dy)


def analyze(path: Path) -> None:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [r for r in rows if "true_f1" in r]
    if not rows:
        _info("no scored rows")
        return

    lanes = sorted({r["lane"] for r in rows})
    feature_keys = sorted(
        {
            k
            for r in rows
            for k, v in r.items()
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and not k.startswith(("true_", "zero_config_", "elapsed"))
            and k not in ("iteration",)
        }
    )

    print("\n" + "=" * 78)
    print("PER-LANE: did the controller commit the best candidate it had?")
    print("=" * 78)
    print(f"{'lane':<34} {'n':>3} {'committed':>10} {'best':>8} {'regret':>8}  {'v0 best?':>9}")
    total_regret = 0.0
    for lane in lanes:
        lr = [r for r in rows if r["lane"] == lane]
        best = max(r["true_f1"] for r in lr)
        com = next((r["true_f1"] for r in lr if r.get("is_committed")), None)
        best_row = max(lr, key=lambda r: r["true_f1"])
        regret = None if com is None else best - com
        if regret:
            total_regret += regret
        print(
            f"{lane:<34} {len(lr):>3} "
            f"{'n/a' if com is None else f'{com:.4f}':>10} {best:>8.4f} "
            f"{'n/a' if regret is None else f'{regret:.4f}':>8}  "
            f"{str(bool(best_row.get('is_v0'))):>9}"
        )
    print(
        f"\n  total regret across lanes: {total_regret:.4f}"
        "   (0 = commit always picked the best candidate available)"
    )

    print("\n" + "=" * 78)
    print("PROXY VALIDITY: Spearman rank correlation vs TRUE F1, within lane")
    print("=" * 78)
    print("  +1 = ranks candidates perfectly   0 = no information   -1 = exactly backwards")
    print(f"\n{'proxy':<44} {'mean rho':>9} {'lanes':>6} {'constant in':>12}")
    scored: list[tuple[float, str, int, int]] = []
    for key in feature_keys:
        rhos, constant = [], 0
        for lane in lanes:
            lr = [r for r in rows if r["lane"] == lane and key in r]
            if len(lr) < 3:
                continue
            xs = [float(r[key]) for r in lr]
            ys = [float(r["true_f1"]) for r in lr]
            if len(set(xs)) == 1:
                constant += 1
                continue
            rho = _spearman(xs, ys)
            if rho is not None:
                rhos.append(rho)
        if rhos:
            scored.append((sum(rhos) / len(rhos), key, len(rhos), constant))
        elif constant:
            scored.append((float("nan"), key, 0, constant))

    ranked = sorted(scored, key=lambda t: (-abs(t[0]) if not math.isnan(t[0]) else 1e9, t[1]))
    for rho, key, n, constant in ranked:
        rho_s = "CONSTANT" if math.isnan(rho) else f"{rho:+.3f}"
        note = f"{constant}/{len(lanes)}" if constant else "-"
        print(f"{key:<44} {rho_s:>9} {n:>6} {note:>12}")

    print("\n" + "=" * 78)
    print("THE TERMS pick_committed ACTUALLY USES")
    print("=" * 78)
    for key in (
        "derived.health_rank",
        "zero_label.overall_confidence",
        "derived.mass_separation",
        "scoring.admitted_fraction",
    ):
        hit = next((t for t in scored if t[1] == key), None)
        if hit is None:
            print(f"  {key:<40} not present")
            continue
        rho, _, n, constant = hit
        rho_s = "CONSTANT (ranks nothing)" if math.isnan(rho) else f"rho={rho:+.3f} over {n} lanes"
        extra = f", constant in {constant}/{len(lanes)} lanes" if constant else ""
        print(f"  {key:<40} {rho_s}{extra}")


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, default=Path("candidates.jsonl"))
    ap.add_argument(
        "--analyze", type=Path, default=None, help="Analyze an existing JSONL instead of running"
    )
    ap.add_argument("--datasets-dir", type=Path, default=_DEFAULT_DATASETS_DIR)
    ap.add_argument(
        "--only", default=None, help="Comma-separated substrings; run only matching lanes"
    )
    ap.add_argument(
        "--generate",
        action="store_true",
        help="Sweep a GENERATED config grid (threshold x splitting) instead of only "
        "the candidates the controller proposed. Measured: the controller's own sets "
        "are too small and too homogeneous to measure proxy validity against -- four "
        "of six lanes offered two candidates of identical F1.",
    )
    args = ap.parse_args()

    if args.analyze is not None:
        analyze(args.analyze)
        return 0

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    lanes = build_lanes(args.datasets_dir, only)
    if not lanes:
        _info("no lanes available (datasets missing?)")
        return 1
    _info(f"{len(lanes)} lanes: {', '.join(ln.name for ln in lanes)}")

    all_rows: list[dict[str, Any]] = []
    with args.out.open("w", encoding="utf-8") as fh:
        for lane in lanes:
            try:
                rows = run_lane_generated(lane) if args.generate else run_lane(lane)
            except SystemExit:
                raise
            except Exception as exc:
                _info(f"{lane.name}: FAILED -- {type(exc).__name__}: {exc}")
                continue
            for r in rows:
                fh.write(json.dumps(r) + "\n")
                fh.flush()
            all_rows += rows

    _info(f"wrote {len(all_rows)} candidate rows to {args.out}")
    analyze(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
