"""Per-rule link-cut matrix (spec 2026-09-11-fs-cut-rule-routing-design, P3).

For one dataset: auto-configure the probabilistic config once, then run the full
pipeline once per arm. Every probabilistic matchkey is pinned to one
``link_cut_rule`` and points at one saved EM model (``model_path``), so the arms
differ only in where the cut lands.

Arms, in run order:
  default         link_cut_rule unset; trains the EM model and saves it
  default_loaded  link_cut_rule unset; loads the saved model -- the baseline
                  every rule arm is compared against, on the same model
  <rule>          one arm per fs_cut_rules.CUT_RULES name, model loaded

Measurement only: nothing here chooses a rule.

Usage:
  python -m scripts.autoconfig_quality.rule_sweep --corpus all --ci --out cut-rules.json
  python -m scripts.autoconfig_quality.rule_sweep --datasets person --out person.json
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")

import argparse  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

from goldenmatch.core.fs_cut_rules import CUT_RULES  # noqa: E402

BASELINE_ARM = "default_loaded"
ARMS: tuple[str, ...] = ("default", BASELINE_ARM, *CUT_RULES)

#: The spec's gate tolerance: a rule is below the default when F1 < default - 0.01.
TOLERANCE = 0.01

#: Env vars that move the cut or void a pin process-wide. A sweep run with any of
#: them set measures something other than the shipped default, so it refuses.
CUT_ENV_VARS = (
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
)


def cut_env_overrides(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The ``CUT_ENV_VARS`` that are set to a non-blank value."""
    env = os.environ if environ is None else environ
    return {k: env[k] for k in CUT_ENV_VARS if (env.get(k) or "").strip()}


def resolve_loader(name: str) -> Callable:
    """Loader for a corpus name.

    The ``scripts.autoconfig_quality.datasets._<name>`` loader when one exists (the
    ``ab_lever`` panel convention), else the head-to-head loader via
    ``_from_headtohead``. An unknown name raises ``KeyError``, so a typo cannot
    silently shrink a run."""
    from scripts.autoconfig_quality import datasets as quality
    from scripts.bench_er_headtohead import datasets as headtohead

    fn = getattr(quality, f"_{name}", None)
    if callable(fn):
        return fn
    if name in headtohead._LOADERS:
        return lambda: quality._from_headtohead(name)
    raise KeyError(f"unknown corpus dataset {name!r}")


def pin_rule(cfg, rule: str | None, model_dir: Path):
    """Copy of ``cfg`` with every probabilistic matchkey pinned to ``rule`` (None
    leaves it unset) and pointed at one saved model per matchkey in ``model_dir``."""
    pinned = []
    for mk in cfg.get_matchkeys():
        if mk.type == "probabilistic":
            mk = mk.model_copy(
                update={"link_cut_rule": rule, "model_path": str(model_dir / f"{mk.name}.json")}
            )
        pinned.append(mk)
    return cfg.model_copy(update={"matchkeys": pinned, "match_settings": None})


def arm_record(arm: str, summary: dict, stats: dict | None, partition: tuple[int, str]) -> dict:
    """One arm's result from ``evaluate_clusters(...).summary()`` and the run's stats.

    ``applied``: every probabilistic matchkey's cut came from the pinned rule.

    ``voided``: the arm measured something other than what it claims, and the sweep
    fails. That happens when the run reported no FS cutoff at all, or when an
    earlier precedence step overrode a pin (``cut_reason`` starts ``link_cut_rule=``).

    A rule this model cannot compute is not applied but not voided: for example
    ``otsu`` on 50 or fewer training pairs, which falls back to the default rule. The
    fallback is itself the finding."""
    report = (stats or {}).get("fs_link_thresholds") or {}
    per_mk = {
        name: {
            key: entry.get(key) for key in ("link_threshold", "source", "cut_rule", "cut_reason")
        }
        for name, entry in sorted(report.items())
    }
    rule = None if arm in ("default", BASELINE_ARM) else arm
    voided = not per_mk or any(
        (entry["cut_reason"] or "").startswith("link_cut_rule=") for entry in per_mk.values()
    )
    applied = bool(per_mk) and (
        rule is None or all(entry["cut_rule"] == rule for entry in per_mk.values())
    )
    pairs, digest = partition
    return {
        "f1": summary["f1"],
        "precision": summary["precision"],
        "recall": summary["recall"],
        "pairs": pairs,
        "digest": digest,
        "matchkeys": per_mk,
        "applied": applied,
        "voided": voided,
    }


def sweep_frame(df, gt: set, model_dir: Path) -> dict:
    """Run every arm on one labelled frame. ``model_dir`` must start empty."""
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

    base = auto_configure_probabilistic_df(df)
    arms: dict[str, dict] = {}
    diagnostics: dict[str, dict | None] = {}
    models_saved: list[str] = []
    for arm in ARMS:
        rule = None if arm in ("default", BASELINE_ARM) else arm
        result = goldenmatch.dedupe_df(df, config=pin_rule(base, rule, model_dir))
        summary = evaluate_clusters(result.clusters, gt).summary()
        arms[arm] = arm_record(arm, summary, result.stats, _partition_fingerprint(result.clusters))
        if arm == "default":
            models_saved = sorted(p.name for p in model_dir.glob("*.json"))
        if arm == BASELINE_ARM:
            report = (result.stats or {}).get("fs_link_thresholds") or {}
            diagnostics = {
                name: entry.get("cut_diagnostics") for name, entry in sorted(report.items())
            }
    baseline = arms[BASELINE_ARM]["f1"]
    return {
        "rows": df.height,
        "gt_pairs": len(gt),
        "arms": arms,
        "cut_diagnostics": diagnostics,
        "models_saved": models_saved,
        "model_reload_delta": float(baseline - arms["default"]["f1"]),
        "below_default": sorted(
            rule
            for rule in CUT_RULES
            if arms[rule]["applied"] and arms[rule]["f1"] < baseline - TOLERANCE
        ),
    }


def sweep_dataset(name: str, work_dir: Path) -> dict | None:
    """Sweep one corpus dataset, or None when it is unavailable or unlabelled."""
    from scripts.autoconfig_quality.corpus import corpus_of

    loaded = resolve_loader(name)()
    if loaded is None:
        return None
    df, gt = loaded
    if not gt:
        return None
    model_dir = work_dir / name
    model_dir.mkdir(parents=True, exist_ok=True)
    record = sweep_frame(df, gt, model_dir)
    record["corpus"] = corpus_of(name)
    return record


def run(argv: list[str]) -> int:
    from scripts.autoconfig_quality.corpus import ci_datasets, select
    from scripts.autoconfig_quality.scorecard import build_scorecard, dumps, gather_meta

    ap = argparse.ArgumentParser(description="Measure every FS link-cut rule per dataset.")
    ap.add_argument("--corpus", choices=["design", "holdout", "all"], default="all")
    ap.add_argument("--datasets", default="", help="comma list; overrides --corpus")
    ap.add_argument("--ci", action="store_true", help="drop local-only datasets from --corpus")
    ap.add_argument("--out", required=True, type=Path, help="scorecard JSON to write")
    ap.add_argument("--require-datasets", default="", help="comma list that MUST be measured")
    ap.add_argument(
        "--allow-cut-env",
        action="store_true",
        help=f"measure even with any of {', '.join(CUT_ENV_VARS)} set",
    )
    args = ap.parse_args(argv)

    overrides = cut_env_overrides()
    if overrides and not args.allow_cut_env:
        print(
            f"refusing to sweep: {sorted(overrides)} set; they move the cut or void a pin "
            "for every arm (pass --allow-cut-env to measure under them on purpose)",
            file=sys.stderr,
        )
        return 2
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"

    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or (
        ci_datasets(args.corpus) if args.ci else list(select(args.corpus))
    )
    for name in names:
        resolve_loader(name)  # fail on a typo before any long run starts

    results: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="gm_cut_rules_") as td:
        for name in names:
            print(f"[cut-rules] {name}", file=sys.stderr)
            record = sweep_dataset(name, Path(td))
            if record is None:
                skipped[name] = "unavailable or unlabelled"
            else:
                results[name] = record

    native_version, git_sha = gather_meta()
    card = build_scorecard(results, native_version=native_version, git_sha=git_sha, skipped=skipped)
    card["meta"]["cut_env_overrides"] = overrides
    card["meta"]["tolerance"] = TOLERANCE
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(card) + "\n", encoding="utf-8")

    required = [d.strip() for d in args.require_datasets.split(",") if d.strip()]
    missing = [d for d in required if d not in results]
    voided = sorted(
        f"{name}:{arm}"
        for name, record in results.items()
        for arm, rec in record["arms"].items()
        if rec["voided"]
    )
    print(
        f"[cut-rules] measured {len(results)}, skipped {len(skipped)} -> {args.out}",
        file=sys.stderr,
    )
    if not results:
        print(
            "FAIL: 0 datasets measured; a sweep that measures nothing cannot pass", file=sys.stderr
        )
        return 1
    if missing:
        print(f"FAIL: required datasets not measured: {missing}", file=sys.stderr)
        return 1
    if voided:
        print(
            f"FAIL: voided arms (a pin was overridden or no FS cutoff reported): {voided}",
            file=sys.stderr,
        )
        return 1
    return 0


def merge_cards(cards: list[dict]) -> dict:
    """One scorecard from per-dataset sweep scorecards (the CI matrix writes one per job)."""
    from scripts.autoconfig_quality.scorecard import build_scorecard

    datasets: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    overrides: dict[str, str] = {}
    shas: set[str] = set()
    natives: set[str] = set()
    for card in cards:
        meta = card["meta"]
        shas.add(meta["git_sha"])
        natives.add(meta["native_version"])
        skipped.update(meta.get("datasets_skipped") or {})
        overrides.update(meta.get("cut_env_overrides") or {})
        for name, record in card["datasets"].items():
            if name in datasets:
                raise ValueError(f"dataset {name!r} appears in two sweep results")
            datasets[name] = record
    if len(shas) > 1:
        raise ValueError(f"sweep results come from different commits: {sorted(shas)}")
    merged = build_scorecard(
        datasets,
        native_version=",".join(sorted(natives)),
        git_sha=next(iter(shas), "unknown"),
        skipped={name: why for name, why in skipped.items() if name not in datasets},
    )
    merged["meta"]["cut_env_overrides"] = overrides
    merged["meta"]["tolerance"] = TOLERANCE
    return merged


def best_rule(record: dict) -> tuple[str, float]:
    """The highest-F1 applied rule arm; ties go to the earlier ``CUT_RULES`` name.
    Falls back to the baseline arm when no rule applied."""
    best: tuple[str, float] | None = None
    for rule in CUT_RULES:
        arm = record["arms"][rule]
        if arm["applied"] and (best is None or arm["f1"] > best[1]):
            best = (rule, arm["f1"])
    return best or (BASELINE_ARM, record["arms"][BASELINE_ARM]["f1"])


def render_markdown(card: dict) -> str:
    """One table row per dataset, design set first, for the CI step summary."""
    order = {"design": 0, "holdout": 1}
    lines = [
        "| dataset | corpus | default F1 | best rule | best F1 | Δ best | below default |",
        "|---|---|---|---|---|---|---|",
    ]
    names = sorted(
        card["datasets"],
        key=lambda n: (order.get(card["datasets"][n].get("corpus"), 2), n),
    )
    for name in names:
        record = card["datasets"][name]
        baseline = record["arms"][BASELINE_ARM]["f1"]
        rule, f1 = best_rule(record)
        below = ", ".join(record.get("below_default") or []) or "none"
        lines.append(
            f"| {name} | {record.get('corpus', '')} | {baseline:.4f} | {rule} | {f1:.4f} "
            f"| {f1 - baseline:+.4f} | {below} |"
        )
    return "\n".join(lines) + "\n"


def merge(argv: list[str]) -> int:
    import json

    from scripts.autoconfig_quality.scorecard import dumps

    ap = argparse.ArgumentParser(description="Merge per-dataset link-cut sweep scorecards.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--summary-md", type=Path, default=None)
    args = ap.parse_args(argv)
    card = merge_cards([json.loads(p.read_text(encoding="utf-8")) for p in args.files])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(card) + "\n", encoding="utf-8")
    if args.summary_md is not None:
        args.summary_md.write_text(render_markdown(card), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "merge":
        return merge(argv[1:])
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
