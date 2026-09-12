"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

musicbrainz_20k: zero-config dedupe_df F1 0.034 vs forced auto_configure_probabilistic_df 0.600. Which half of the
zero-config config causes it? Four arms, each in its own child process, crossing the two configs' BLOCKING with their
MATCHKEYS: (zc blocking, zc matchkeys) / (fs, fs) / (zc blocking, fs matchkeys) / (fs blocking, zc matchkeys).
A fifth arm, zc_ev5, keeps the zero-config config whole and pins its FS link cut to evidence_5 (the rule the router
gives forced FS, whose lambda .076 clears the midpoint_binds_wide row's lambda < 0.1 gate; zero-config's .12 does not).

Usage, from the repo root:
  python -m scripts.spike.mb_ablation --out DIR [--dataset musicbrainz_20k] [--arm ARM]
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

ARMS = ("zc_zc", "fs_fs", "zcblock_fsmk", "fsblock_zcmk", "zc_ev5")


def _configs(df):
    from goldenmatch.core.autoconfig import auto_configure_df, auto_configure_probabilistic_df

    return auto_configure_df(df, confidence_required=False), auto_configure_probabilistic_df(df)


def _arm_config(arm: str, zc, fs):
    if arm == "zc_nobucket":
        # The zero-config config whole, minus its backend="bucket" (forced FS runs the default backend).
        return zc.model_copy(update={"backend": None})
    if arm == "zc_nb_noobs":
        # zc_nobucket, and the matchkeys' link_threshold_observed cleared.
        mks = [
            mk.model_copy(update={"link_threshold_observed": None})
            if mk.type == "probabilistic"
            else mk
            for mk in zc.get_matchkeys()
        ]
        return zc.model_copy(update={"backend": None, "matchkeys": mks, "match_settings": None})
    if arm == "zc_nb_nopre":
        # zc_nobucket, and the private _preflight_report cleared.
        cfg = zc.model_copy(update={"backend": None})
        cfg._preflight_report = None
        return cfg
    if arm == "zc_ev5":
        mks = [
            mk.model_copy(update={"link_cut_rule": "evidence_5"})
            if mk.type == "probabilistic"
            else mk
            for mk in zc.get_matchkeys()
        ]
        return zc.model_copy(update={"matchkeys": mks, "match_settings": None})
    block_src, mk_src = {
        "zc_zc": (zc, zc),
        "fs_fs": (fs, fs),
        "zcblock_fsmk": (zc, fs),
        "fsblock_zcmk": (fs, zc),
    }[arm]
    return mk_src.model_copy(update={"blocking": block_src.blocking})


def child(dataset: str, arm: str, out: Path) -> None:
    import goldenmatch

    from scripts.autoconfig_quality.rule_sweep import resolve_loader
    from scripts.spike.fs_loss_probe import _blocking_recall, _cluster

    df, gt = resolve_loader(dataset)()
    gt = {(min(a, b), max(a, b)) for a, b in gt}
    zc, fs = _configs(df)
    cfg = _arm_config(arm, zc, fs)
    record: dict = {"dataset": dataset, "arm": arm}
    if arm in ("zc_zc", "fs_fs"):
        record["config"] = cfg.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    t0 = time.perf_counter()
    try:
        result = goldenmatch.dedupe_df(df, config=cfg, confidence_required=False)
        record.update(
            **_cluster(result, gt),
            scored_pairs=len(result.scored_pairs or []),
            review_pairs=len(result.review_pairs or []),
            fs_link_thresholds={
                k: {
                    "link_threshold": v.get("link_threshold"),
                    "cut_rule": v.get("cut_rule"),
                    "cut_reason": v.get("cut_reason"),
                    "refit": (v.get("refit") or {}).get("reason"),
                    "lambda": (v.get("cut_diagnostics") or {}).get("proportion_matched"),
                    "midpoint_bits": (v.get("cut_diagnostics") or {}).get("midpoint_bits"),
                    "prior_bits": (v.get("cut_diagnostics") or {}).get("prior_bits"),
                    "field_weight_spans": (v.get("cut_diagnostics") or {}).get(
                        "field_weight_spans"
                    ),
                }
                for k, v in ((result.stats or {}).get("fs_link_thresholds") or {}).items()
            },
        )
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-3000:])
    record["seconds"] = round(time.perf_counter() - t0, 1)
    record.update(_blocking_recall(df, cfg, gt))
    (out / f"{dataset}-{arm}.json").write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="THROWAWAY: blocking x matchkey ablation of zero-config vs forced FS."
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dataset", default="musicbrainz_20k")
    ap.add_argument("--arm", default=None)
    ap.add_argument("--arms", default="", help="comma list for the driver; default all")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.arm:
        child(args.dataset, args.arm, args.out)
        return 0
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    arms = [a.strip() for a in args.arms.split(",") if a.strip()] or list(ARMS)
    for arm in arms:
        if (args.out / f"{args.dataset}-{arm}.json").exists():
            continue
        t0 = time.perf_counter()
        argv_child = [
            sys.executable,
            "-m",
            "scripts.spike.mb_ablation",
            "--dataset",
            args.dataset,
            "--arm",
            arm,
            "--out",
            str(args.out),
        ]
        try:
            rc = subprocess.run(argv_child, env=env, timeout=3600).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        print(
            f"[mb-ablation] {args.dataset}:{arm} rc={rc} {time.perf_counter() - t0:.0f}s",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
