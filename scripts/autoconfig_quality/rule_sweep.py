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
    # Passes a pair_filter to load_or_train_em, which stops model_path from
    # saving, so arms would retrain their own model instead of sharing one.
    "GOLDENMATCH_FS_SIGNATURE_PRUNE",
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


def arm_record(
    arm: str,
    summary: dict,
    stats: dict | None,
    partition: tuple[int, str],
    expected_matchkeys: list[str],
) -> dict:
    """One arm's result from ``evaluate_clusters(...).summary()`` and the run's stats.

    ``expected_matchkeys``: the probabilistic matchkey names ``pin_rule`` pinned.
    Every one of them must show up in the ``fs_link_thresholds`` report or the arm
    is voided -- a matchkey silently missing from the report is not distinguishable
    from one that never ran.

    ``applied``: every expected matchkey is present and its cut came from the
    pinned rule.

    ``voided``: the arm measured something other than what it claims, and the
    sweep fails. That happens when the run reported no FS cutoff at all, an
    expected matchkey is missing from the report, or when an earlier precedence
    step overrode a pin (``cut_reason`` starts ``link_cut_rule=``).

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
    missing_expected = [name for name in expected_matchkeys if name not in per_mk]
    voided = (
        not per_mk
        or bool(missing_expected)
        or any(
            (entry["cut_reason"] or "").startswith("link_cut_rule=") for entry in per_mk.values()
        )
    )
    applied = (
        bool(per_mk)
        and not missing_expected
        and (rule is None or all(per_mk[name]["cut_rule"] == rule for name in expected_matchkeys))
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


def _model_hashes(model_dir: Path) -> dict[str, str]:
    """``{file name: sha256 hex of bytes}`` for every saved model in ``model_dir``."""
    import hashlib

    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(model_dir.glob("*.json"))
    }


def sweep_frame(df, gt: set, model_dir: Path) -> dict:
    """Run every arm on one labelled frame. ``model_dir`` must start empty."""
    import time

    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

    start = time.perf_counter()
    base = auto_configure_probabilistic_df(df)
    probabilistic_matchkeys = sorted(
        mk.name for mk in base.get_matchkeys() if mk.type == "probabilistic"
    )
    arms: dict[str, dict] = {}
    diagnostics: dict[str, dict | None] = {}
    models_saved: list[str] = []
    hashes_after_default: dict[str, str] = {}
    for arm in ARMS:
        rule = None if arm in ("default", BASELINE_ARM) else arm
        result = goldenmatch.dedupe_df(df, config=pin_rule(base, rule, model_dir))
        summary = evaluate_clusters(result.clusters, gt).summary()
        arms[arm] = arm_record(
            arm,
            summary,
            result.stats,
            _partition_fingerprint(result.clusters),
            probabilistic_matchkeys,
        )
        if arm == "default":
            models_saved = sorted(p.name for p in model_dir.glob("*.json"))
            hashes_after_default = _model_hashes(model_dir)
        if arm == BASELINE_ARM:
            report = (result.stats or {}).get("fs_link_thresholds") or {}
            diagnostics = {
                name: entry.get("cut_diagnostics") for name, entry in sorted(report.items())
            }
    hashes_after_last = _model_hashes(model_dir)
    baseline = arms[BASELINE_ARM]["f1"]
    model_complete = bool(models_saved) and sorted(models_saved) == sorted(
        f"{name}.json" for name in probabilistic_matchkeys
    )
    model_stable = (
        bool(hashes_after_default)
        and bool(hashes_after_last)
        and hashes_after_default == hashes_after_last
    )
    return {
        "rows": df.height,
        "gt_pairs": len(gt),
        "probabilistic_matchkeys": probabilistic_matchkeys,
        "arms": arms,
        "cut_diagnostics": diagnostics,
        "models_saved": models_saved,
        "model_complete": model_complete,
        "model_stable": model_stable,
        "reload_partition_match": arms["default"]["digest"] == arms[BASELINE_ARM]["digest"],
        "model_reload_delta": float(baseline - arms["default"]["f1"]),
        "below_default": sorted(
            rule
            for rule in CUT_RULES
            if arms[rule]["applied"] and arms[rule]["f1"] < baseline - TOLERANCE
        ),
        "wall_seconds": round(time.perf_counter() - start, 1),
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
    card["meta"]["goldenmatch_env"] = {
        k: v for k, v in sorted(os.environ.items()) if k.startswith("GOLDENMATCH_")
    }
    card["meta"]["pythonhashseed"] = os.environ.get("PYTHONHASHSEED")

    required = [d.strip() for d in args.require_datasets.split(",") if d.strip()]
    failures: list[str] = []
    for name in required:
        if name not in results:
            failures.append(f"{name}: required dataset not measured")
    for name, record in results.items():
        for arm, rec in record["arms"].items():
            if rec["voided"]:
                failures.append(f"{name}:{arm} voided")
        if not record.get("model_complete"):
            failures.append(f"{name}: shared EM model not saved for every probabilistic matchkey")
        if not record.get("model_stable"):
            failures.append(f"{name}: shared EM model changed between arms")
    if not results:
        failures.append("0 datasets measured")
    failures.sort()

    card["meta"]["failures"] = failures
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(card) + "\n", encoding="utf-8")

    print(
        f"[cut-rules] measured {len(results)}, skipped {len(skipped)} -> {args.out}",
        file=sys.stderr,
    )
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


def merge_cards(cards: list[dict], expected: list[str] | None = None) -> dict:
    """One scorecard from per-dataset sweep scorecards (the CI matrix writes one per job)."""
    from scripts.autoconfig_quality.scorecard import build_scorecard

    if not cards:
        raise ValueError("no sweep results to merge")

    datasets: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    overrides: dict[str, str] = {}
    shas: set[str] = set()
    natives: set[str] = set()
    failures: list[str] = []
    for card in cards:
        meta = card["meta"]
        shas.add(meta["git_sha"])
        natives.add(meta["native_version"])
        skipped.update(meta.get("datasets_skipped") or {})
        overrides.update(meta.get("cut_env_overrides") or {})
        failures.extend(meta.get("failures") or [])
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
    merged["meta"]["failures"] = sorted(failures)
    merged["meta"]["missing"] = sorted(set(expected) - set(datasets)) if expected else []
    return merged


def best_rule(record: dict) -> tuple[str, float]:
    """The highest-F1 applied rule arm; ties go to the earlier ``CUT_RULES`` name.
    Falls back to the baseline arm (``BASELINE_ARM``) when no rule applied --
    callers rendering this for humans should treat that sentinel as "no rule
    applied", not as a real rule name."""
    best: tuple[str, float] | None = None
    for rule in CUT_RULES:
        arm = record["arms"][rule]
        if arm["applied"] and (best is None or arm["f1"] > best[1]):
            best = (rule, arm["f1"])
    return best or (BASELINE_ARM, record["arms"][BASELINE_ARM]["f1"])


def _not_applied_rules(record: dict) -> list[str]:
    """Rules whose arm neither applied nor voided (e.g. otsu falling back
    on too few training pairs)."""
    return sorted(
        rule
        for rule in CUT_RULES
        if not record["arms"][rule]["applied"] and not record["arms"][rule]["voided"]
    )


def render_markdown(card: dict, holdout_card: dict | None = None) -> str:
    """The design table (full detail), and -- when ``holdout_card`` is given -- the
    held-out gate view (counts only, no rule names or per-rule F1: the held-out set
    stays out of view of row-writing). Missing datasets (``card["meta"]["missing"]``)
    render as an all-MISSING row in whichever table matches their corpus."""
    from scripts.autoconfig_quality.corpus import corpus_of

    missing = sorted(card["meta"].get("missing") or [])
    design_missing = [n for n in missing if corpus_of(n) != "holdout"]
    holdout_missing = [n for n in missing if corpus_of(n) == "holdout"]

    lines = [
        "### Design",
        "",
        "| dataset | default F1 | best rule | best F1 | Δ best | below default"
        " | not applied | reload digest |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(card["datasets"]):
        record = card["datasets"][name]
        baseline = record["arms"][BASELINE_ARM]["f1"]
        rule, f1 = best_rule(record)
        label = "no rule applied" if rule == BASELINE_ARM else rule
        below = ", ".join(record.get("below_default") or []) or "none"
        not_applied = ", ".join(_not_applied_rules(record)) or "none"
        digest = "same" if record.get("reload_partition_match", True) else "DIFFERS"
        lines.append(
            f"| {name} | {baseline:.4f} | {label} | {f1:.4f} "
            f"| {f1 - baseline:+.4f} | {below} | {not_applied} | {digest} |"
        )
    for name in design_missing:
        lines.append(
            f"| {name} | MISSING | MISSING | MISSING | MISSING | MISSING | MISSING | MISSING |"
        )

    out = "\n".join(lines) + "\n"

    if holdout_card is not None:
        h_lines = [
            "",
            "### Held-out (gate view)",
            "",
            "| dataset | default F1 | rules below default | rules not applied |",
            "|---|---|---|---|",
        ]
        for name in sorted(holdout_card["datasets"]):
            record = holdout_card["datasets"][name]
            baseline = record["arms"][BASELINE_ARM]["f1"]
            below_n = len(record.get("below_default") or [])
            not_applied_n = len(_not_applied_rules(record))
            h_lines.append(f"| {name} | {baseline:.4f} | {below_n} | {not_applied_n} |")
        for name in holdout_missing:
            h_lines.append(f"| {name} | MISSING | MISSING | MISSING |")
        out += "\n".join(h_lines) + "\n"

    failures = card["meta"].get("failures") or []
    if failures:
        out += "\n**Failures**\n\n" + "\n".join(f"- {f}" for f in failures) + "\n"

    return out


def _split_by_corpus(card: dict) -> tuple[dict, dict]:
    """``card``'s datasets split into (design + unlisted, holdout), same meta."""
    from scripts.autoconfig_quality.corpus import corpus_of

    design = {n: r for n, r in card["datasets"].items() if corpus_of(n) != "holdout"}
    holdout = {n: r for n, r in card["datasets"].items() if corpus_of(n) == "holdout"}
    return {"meta": card["meta"], "datasets": design}, {"meta": card["meta"], "datasets": holdout}


def merge(argv: list[str]) -> int:
    import json

    from scripts.autoconfig_quality.scorecard import dumps

    ap = argparse.ArgumentParser(description="Merge per-dataset link-cut sweep scorecards.")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path, help="design + unlisted datasets only")
    ap.add_argument("--holdout-out", type=Path, default=None, help="held-out datasets, gate input")
    ap.add_argument("--summary-md", type=Path, default=None)
    ap.add_argument("--expect-json", default=None, help="JSON array of expected dataset names")
    args = ap.parse_args(argv)

    expected = json.loads(args.expect_json) if args.expect_json else None
    card = merge_cards(
        [json.loads(p.read_text(encoding="utf-8")) for p in args.files], expected=expected
    )
    design_card, holdout_card = _split_by_corpus(card)

    if holdout_card["datasets"] and args.holdout_out is None:
        print(
            "FAIL: merged results include held-out datasets but --holdout-out was not given",
            file=sys.stderr,
        )
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(design_card) + "\n", encoding="utf-8")
    if args.holdout_out is not None:
        args.holdout_out.parent.mkdir(parents=True, exist_ok=True)
        args.holdout_out.write_text(dumps(holdout_card) + "\n", encoding="utf-8")
    if args.summary_md is not None:
        rendered = render_markdown(design_card, holdout_card if holdout_card["datasets"] else None)
        args.summary_md.write_text(rendered, encoding="utf-8")

    failures = card["meta"].get("failures") or []
    missing = card["meta"].get("missing") or []
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    for name in missing:
        print(f"FAIL: missing dataset {name}", file=sys.stderr)
    return 1 if (failures or missing) else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "merge":
        return merge(argv[1:])
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
