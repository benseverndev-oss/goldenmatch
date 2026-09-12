"""Per-rule link-cut matrix (spec 2026-09-11-fs-cut-rule-routing-design, P3; routed arm and labelled metric P4).

For one dataset: auto-configure the probabilistic config once, then run the full
pipeline once per arm, each arm in its own child process under a memory cap and a
timeout (``run_arm``). Every probabilistic matchkey is pinned to one
``link_cut_rule`` and points at one saved EM model (``model_path``), so the arms
differ only in where the cut lands.

Arms, in run order:
  default         link_cut_rule unset; trains the EM model and saves it
  default_loaded  link_cut_rule unset; loads the saved model -- the baseline
                  every rule arm is compared against, on the same model
  <rule>          one arm per fs_cut_rules.CUT_RULES name, model loaded
  routed          link_cut_rule unset, GOLDENMATCH_FS_CUT_ROUTER=on, model loaded

Measurement only: nothing here chooses a rule (cut_gate.py gates rows on this output).

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

from goldenmatch.core.fs_cut_rules import CUT_RULES, ROUTED_REASON_PREFIX  # noqa: E402

BASELINE_ARM = "default_loaded"
#: link_cut_rule unset, GOLDENMATCH_FS_CUT_ROUTER on, model loaded: the shipped routing table
#: end to end. Its partition must equal the arm pinning the rule it routed to.
ROUTED_ARM = "routed"
ARMS: tuple[str, ...] = ("default", BASELINE_ARM, *CUT_RULES, ROUTED_ARM)

#: The spec's gate tolerance: a rule is below the default when F1 < default - 0.01.
TOLERANCE = 0.01

ROUTER_ENV = "GOLDENMATCH_FS_CUT_ROUTER"

#: Env vars that move the cut or void a pin process-wide. A sweep run with any of
#: them set measures something other than the unrouted default (the gate reference), so it refuses.
CUT_ENV_VARS = (
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
    # Passes a pair_filter to load_or_train_em, which stops model_path from
    # saving, so arms would retrain their own model instead of sharing one.
    "GOLDENMATCH_FS_SIGNATURE_PRUNE",
    # The sweep sets it per child (arm_env): on for ROUTED_ARM, off for every other arm.
    ROUTER_ENV,
)


def pinned_rule(arm: str) -> str | None:
    """The ``link_cut_rule`` an arm pins: its own name for a rule arm, None otherwise."""
    return arm if arm in CUT_RULES else None


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
    rule = pinned_rule(arm)
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


def labelled_summary(clusters: dict, labels: dict[tuple[int, int], bool]) -> dict:
    """Precision / recall / F1 over the labelled pairs only.

    A labelled match inside one predicted cluster is a TP, a labelled non-match inside one is
    an FP, and a labelled match split apart is an FN. Unlabelled pairs count for nothing."""
    from goldenmatch.core.evaluate import EvalResult

    cluster_of: dict[int, object] = {}
    for cid, info in clusters.items():
        for member in info.get("members", []):
            cluster_of[member] = cid
    tp = fp = fn = 0
    for (i, j), is_match in labels.items():
        together = i in cluster_of and cluster_of[i] == cluster_of.get(j)
        if is_match and together:
            tp += 1
        elif is_match:
            fn += 1
        elif together:
            fp += 1
    summary = EvalResult(tp=tp, fp=fp, fn=fn).summary()
    return {
        **{key: summary[key] for key in ("f1", "precision", "recall")},
        "labelled_pairs": len(labels),
    }


def gate_metric_of(name: str) -> str:
    """``"labelled"`` for a DeepMatcher/Magellan dataset (F1 over labelled pairs only), else
    ``"f1"``."""
    from scripts.bench_er_headtohead.datasets import MAGELLAN_SUBDIRS

    return "labelled" if name in MAGELLAN_SUBDIRS else "f1"


def metric_value(arm_record: dict, metric: str) -> float | None:
    """An arm's gate metric: ``f1``, or the labelled-pairs F1; None when not measured."""
    if metric == "labelled":
        labelled = arm_record.get("labelled")
        return None if labelled is None else labelled["f1"]
    return arm_record.get("f1")


#: Watchdog limits for one arm's child process. The memory cap leaves headroom on
#: the 16 GB hosted runner, so an over-merging rule is killed and recorded before
#: the runner itself dies and takes every other arm's measurement with it.
ARM_MEMORY_CAP_MB = 12000
ARM_TIMEOUT_S = 3600
#: The child's RSS misses the parent's memory, spikes between polls and swap, so an
#: arm is also killed when the whole machine's available memory drops below this.
MIN_AVAILABLE_MB = 1500
#: Wall budget for all of one dataset's arms, from the first arm's start; None means
#: no budget. CI sets it so every arm fits inside the job's own timeout.
DATASET_BUDGET_S: float | None = None
ARM_MEMORY_CAP_ENV = "GOLDENMATCH_CUT_RULES_ARM_MEM_MB"
ARM_TIMEOUT_ENV = "GOLDENMATCH_CUT_RULES_ARM_TIMEOUT_S"
MIN_AVAILABLE_ENV = "GOLDENMATCH_CUT_RULES_MIN_AVAILABLE_MB"
DATASET_BUDGET_ENV = "GOLDENMATCH_CUT_RULES_DATASET_BUDGET_S"
#: How long to wait for a killed process to go away before giving up on reaping it.
KILL_WAIT_S = 10


def _limit(value: float | None, env_var: str, default: float | None) -> float | None:
    """``value`` when given, else the env override, else ``default``."""
    if value is not None:
        return value
    raw = (os.environ.get(env_var) or "").strip()
    return float(raw) if raw else default


# The two timing fields on every arm record:
#   wall_seconds     pipeline time, measured inside execute_arm by the child; None
#                    when no pipeline result came back (killed, crashed, not started).
#   process_seconds  the parent's clock from starting the child to its exit or kill,
#                    including interpreter start-up and loading; 0.0 if never started.


def killed_record(
    status: str,
    returncode: int | None,
    peak_rss_mb: float,
    process_seconds: float,
    kill_reason: str | None = None,
) -> dict:
    """The record of an arm whose child was killed, crashed or never started: no
    measurement, not voided (nothing claimed a result it did not measure), and the
    reason.

    ``kill_reason`` says which trigger fired: ``rss_cap`` / ``available_floor`` for
    ``memory_cap``, ``dataset_budget`` for a timeout the budget left no time to start,
    else None."""
    return {
        "f1": None,
        "precision": None,
        "recall": None,
        "pairs": None,
        "digest": None,
        "labelled": None,
        "matchkeys": {},
        "applied": False,
        "voided": False,
        "crashed": status,
        "kill_reason": kill_reason,
        "returncode": returncode,
        "peak_rss_mb": round(peak_rss_mb, 1),
        "wall_seconds": None,
        "process_seconds": round(process_seconds, 1),
    }


def _tree_rss_mb(proc) -> float:
    """RSS of ``proc`` plus all its recursive children, in MB. On Windows a venv
    ``python.exe`` is a launcher whose real interpreter is a child, so the tree
    is what holds the memory."""
    import psutil

    try:
        procs = [proc, *proc.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0.0
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total / 2**20


def _kill_tree(pid: int) -> None:
    """Kill ``pid`` and every descendant; no POSIX-only signals. Never raises: it
    runs inside error handlers, where a cleanup error must not mask the original.

    Waits only for the descendants. The root is the caller's ``Popen`` child, which
    the caller reaps (``_reap``): if psutil reaped it first, ``Popen`` would get
    ECHILD on Linux and report a killed arm as ``returncode`` 0."""
    import psutil

    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    try:
        descendants = root.children(recursive=True)
    except psutil.Error:
        descendants = []
    for p in [*descendants, root]:
        try:
            p.kill()
        except psutil.Error:
            pass
    try:
        psutil.wait_procs(descendants, timeout=KILL_WAIT_S)
    except psutil.Error:
        pass


def _reap(popen) -> None:
    """Wait for a killed ``Popen`` child, but never forever."""
    import subprocess

    try:
        popen.wait(timeout=KILL_WAIT_S)
    except subprocess.TimeoutExpired:
        pass


def run_arm(
    argv: list[str],
    out_path: Path,
    arm: str,
    expected_matchkeys: list[str],
    *,
    env: dict[str, str] | None = None,
    memory_cap_mb: float | None = None,
    timeout_s: float | None = None,
    min_available_mb: float | None = None,
    poll_s: float = 0.5,
) -> tuple[dict, dict | None]:
    """Run one arm's child (``argv``) under the watchdog. Never raises for the child.

    Returns ``(arm record, child payload)``. The payload is None when the child was
    killed (``memory_cap`` / ``timeout``) or ``crashed`` (non-zero exit or no JSON
    at ``out_path``); the record then says which, instead of the sweep dying.
    ``memory_cap`` fires on the child tree's RSS (``rss_cap``) or on the machine's
    available memory (``available_floor``)."""
    import json
    import subprocess
    import time

    import psutil

    cap = _limit(memory_cap_mb, ARM_MEMORY_CAP_ENV, ARM_MEMORY_CAP_MB)
    limit = _limit(timeout_s, ARM_TIMEOUT_ENV, ARM_TIMEOUT_S)
    floor = _limit(min_available_mb, MIN_AVAILABLE_ENV, MIN_AVAILABLE_MB)
    out_path.unlink(missing_ok=True)  # a stale result must not pass for this run's
    start = time.perf_counter()
    popen = subprocess.Popen(argv, env=env)
    peak = 0.0
    status: str | None = None
    kill_reason: str | None = None
    try:
        proc = psutil.Process(popen.pid)
        while True:
            peak = max(peak, _tree_rss_mb(proc))
            if peak > cap:
                status, kill_reason = "memory_cap", "rss_cap"
            elif psutil.virtual_memory().available / 2**20 < floor:
                status, kill_reason = "memory_cap", "available_floor"
            elif time.perf_counter() - start > limit:
                status = "timeout"
            if status is not None:
                _kill_tree(popen.pid)
                _reap(popen)
                break
            try:
                popen.wait(timeout=poll_s)
                break
            except subprocess.TimeoutExpired:
                continue
    except psutil.NoSuchProcess:
        _reap(popen)  # exited before the first sample; classified below
    except BaseException:
        _kill_tree(popen.pid)  # a cancelled sweep must not orphan a capped child
        _reap(popen)
        raise
    process_seconds = time.perf_counter() - start
    returncode = popen.returncode
    if status is not None:
        # A killed arm never reports a clean exit: 0 here could only be a child that
        # finished in the instant between the sample and the kill, or a lost reap.
        killed_rc = None if returncode == 0 else returncode
        return killed_record(status, killed_rc, peak, process_seconds, kill_reason), None
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    if returncode != 0 or payload is None:
        return killed_record("crashed", returncode, peak, process_seconds), None
    record = arm_record(
        arm,
        payload["summary"],
        {"fs_link_thresholds": payload["report"]},
        tuple(payload["partition"]),
        expected_matchkeys,
    )
    # wall_seconds from the child's pipeline, process_seconds from the parent's clock.
    record.update(
        crashed=None,
        peak_rss_mb=round(peak, 1),
        wall_seconds=payload["wall_seconds"],
        process_seconds=round(process_seconds, 1),
        labelled=payload.get("labelled"),
    )
    return record, payload


def execute_arm(df, gt: set, cfg, arm: str, model_dir: Path, labels: dict | None = None) -> dict:
    """Run one arm on one labelled frame and return its JSON-able result.

    The only place an arm's pipeline runs: the ``arm`` child process calls it, and so does any
    in-process caller. ``labels`` (``datasets.labelled_pairs``) adds the labelled-pairs
    metric."""
    import time

    import goldenmatch
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.bench_er_headtohead.ab_lever import _partition_fingerprint

    start = time.perf_counter()
    rule = pinned_rule(arm)
    result = goldenmatch.dedupe_df(df, config=pin_rule(cfg, rule, model_dir))
    summary = evaluate_clusters(result.clusters, gt).summary()
    pairs, digest = _partition_fingerprint(result.clusters)
    return {
        "summary": {key: summary[key] for key in ("f1", "precision", "recall")},
        "report": (result.stats or {}).get("fs_link_thresholds") or {},
        "partition": [pairs, digest],
        "wall_seconds": round(time.perf_counter() - start, 1),
        "labelled": labelled_summary(result.clusters, labels) if labels is not None else None,
    }


def arm_env(arm: str | None = None) -> dict[str, str]:
    """A child's env: the parent's, plus one hash seed shared by every arm, no autoconfig
    memory, and the link-cut router on for ``ROUTED_ARM`` only."""
    return {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        ROUTER_ENV: "on" if arm == ROUTED_ARM else "off",
    }


def arm_argv(name: str, config_path: Path, arm: str, model_dir: Path, out_path: Path) -> list[str]:
    """The command that runs one arm in a child process (the ``arm`` subcommand)."""
    return [
        sys.executable,
        "-m",
        "scripts.autoconfig_quality.rule_sweep",
        "arm",
        "--dataset",
        name,
        "--config",
        str(config_path),
        "--arm",
        arm,
        "--model-dir",
        str(model_dir),
        "--out",
        str(out_path),
    ]


def arm_main(argv: list[str]) -> int:
    """Hidden ``arm`` subcommand: one arm on one dataset, result JSON at ``--out``."""
    import json

    from goldenmatch.config.schemas import GoldenMatchConfig

    ap = argparse.ArgumentParser(description="Run one link-cut sweep arm (the sweep's child).")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    loaded = resolve_loader(args.dataset)()
    if loaded is None or not loaded[1]:
        print(f"arm: dataset {args.dataset!r} unavailable or unlabelled", file=sys.stderr)
        return 1
    df, gt = loaded
    cfg = GoldenMatchConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    from scripts.autoconfig_quality.datasets import labelled_pairs

    payload = execute_arm(
        df, gt, cfg, args.arm, args.model_dir, labels=labelled_pairs(args.dataset)
    )
    args.out.write_text(json.dumps(payload), encoding="utf-8")
    return 0


def routed_check(arms: dict[str, dict]) -> tuple[dict[str, str | None], bool | None]:
    """What the router picked per matchkey on the routed arm, and whether that arm's partition
    equals the arm pinning the same rule (the baseline when no row fired).

    ``{matchkey: rule}`` holds the rule only where ``cut_reason`` says a row routed it, else
    None. The match is None when there is nothing to compare:
    - no routed arm;
    - a crashed or voided arm on either side;
    - matchkeys routed to different rules, which no single pinned arm measures."""
    routed = arms.get(ROUTED_ARM)
    if routed is None or routed.get("crashed") or routed.get("voided"):
        return {}, None
    rules = {
        mk: entry["cut_rule"]
        if (entry.get("cut_reason") or "").startswith(ROUTED_REASON_PREFIX)
        else None
        for mk, entry in routed["matchkeys"].items()
    }
    picked = set(rules.values())
    if len(picked) != 1:
        return rules, None
    target = arms.get(picked.pop() or BASELINE_ARM)
    if target is None or target.get("crashed") or target.get("voided"):
        return rules, None
    return rules, routed["digest"] == target["digest"]


def sweep_dataset(
    name: str,
    work_dir: Path,
    arms: tuple[str, ...] = ARMS,
    budget_s: float | None = None,
) -> dict | None:
    """Sweep one corpus dataset, or None when it is unavailable or unlabelled.

    Loads and auto-configures once, writes the config every arm reads to
    ``<work_dir>/<name>/config.json``, then runs each arm in its own child process
    under the watchdog (``run_arm``).

    ``budget_s`` (else ``GOLDENMATCH_CUT_RULES_DATASET_BUDGET_S``, else no budget)
    bounds all the arms together, from the first arm's start: each arm's timeout is
    at most its share of what is left, and once the budget is spent the remaining
    arms are recorded as ``timeout`` without being started."""
    import time

    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    from scripts.autoconfig_quality.corpus import corpus_of

    loaded = resolve_loader(name)()
    if loaded is None:
        return None
    df, gt = loaded
    if not gt:
        return None
    start = time.perf_counter()
    dataset_dir = work_dir / name
    model_dir = dataset_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    cfg = auto_configure_probabilistic_df(df)
    config_path = dataset_dir / "config.json"
    config_path.write_text(cfg.model_dump_json(), encoding="utf-8")
    probabilistic_matchkeys = sorted(
        mk.name for mk in cfg.get_matchkeys() if mk.type == "probabilistic"
    )
    rows, gt_pairs = df.height, len(gt)
    del loaded, df, gt  # every child reloads the dataset; the parent holds none of it

    budget = _limit(budget_s, DATASET_BUDGET_ENV, DATASET_BUDGET_S)
    arm_timeout = _limit(None, ARM_TIMEOUT_ENV, ARM_TIMEOUT_S)
    # Held-out per-rule F1 stays out of CI logs, like it stays out of the gate view.
    holdout = corpus_of(name) == "holdout"
    metric = gate_metric_of(name)
    records: dict[str, dict] = {}
    diagnostics: dict[str, dict | None] = {}
    models_saved: list[str] = []
    hashes_after_default: dict[str, str] = {}
    arms_start = time.perf_counter()
    for index, arm in enumerate(arms):
        timeout_s = arm_timeout
        if budget is not None:
            remaining = budget - (time.perf_counter() - arms_start)
            timeout_s = min(arm_timeout, remaining / (len(arms) - index))
        if budget is not None and timeout_s <= 0:
            records[arm] = killed_record("timeout", None, 0.0, 0.0, "dataset_budget")
            payload = None
        else:
            out_path = dataset_dir / f"arm_{arm}.json"
            argv = arm_argv(name, config_path, arm, model_dir, out_path)
            records[arm], payload = run_arm(
                argv, out_path, arm, probabilistic_matchkeys, env=arm_env(arm), timeout_s=timeout_s
            )
        rec = records[arm]
        f1_part = "" if holdout else f" f1={rec['f1']}"
        print(
            f"[cut-rules] {name}:{arm} crashed={rec['crashed']}{f1_part} "
            f"peak_rss_mb={rec['peak_rss_mb']} wall_seconds={rec['wall_seconds']} "
            f"process_seconds={rec['process_seconds']}",
            file=sys.stderr,
        )
        if arm == "default":
            models_saved = sorted(p.name for p in model_dir.glob("*.json"))
            hashes_after_default = _model_hashes(model_dir)
        if arm == BASELINE_ARM and payload is not None:
            diagnostics = {
                mk: entry.get("cut_diagnostics") for mk, entry in sorted(payload["report"].items())
            }
    hashes_after_last = _model_hashes(model_dir)
    routed_rules, routed_matches = routed_check(records)
    baseline_f1 = records[BASELINE_ARM]["f1"]
    baseline_gate = metric_value(records[BASELINE_ARM], metric)
    rules = [arm for arm in arms if arm in CUT_RULES]
    # Without both baselines there is nothing to compare a rule against; run() fails it.
    baseline_ok = not records["default"]["crashed"] and not records[BASELINE_ARM]["crashed"]
    model_complete = bool(models_saved) and sorted(models_saved) == sorted(
        f"{mk}.json" for mk in probabilistic_matchkeys
    )
    model_stable = (
        bool(hashes_after_default)
        and bool(hashes_after_last)
        and hashes_after_default == hashes_after_last
    )
    return {
        "corpus": corpus_of(name),
        "rows": rows,
        "gt_pairs": gt_pairs,
        "probabilistic_matchkeys": probabilistic_matchkeys,
        "arms": records,
        "cut_diagnostics": diagnostics,
        "gate_metric": metric,
        "routed_rules": routed_rules,
        "routed_matches": routed_matches,
        "models_saved": models_saved,
        "model_complete": model_complete,
        "model_stable": model_stable,
        "reload_partition_match": (
            records["default"]["digest"] == records[BASELINE_ARM]["digest"] if baseline_ok else None
        ),
        "model_reload_delta": (
            float(baseline_f1 - records["default"]["f1"]) if baseline_ok else None
        ),
        # A rule arm that was killed or crashed is worse than the baseline. Magellan datasets
        # compare on the labelled-pairs metric (gate_metric).
        "below_default": sorted(
            rule
            for rule in rules
            if baseline_ok
            and (
                records[rule]["crashed"]
                or (
                    records[rule]["applied"]
                    and baseline_gate is not None
                    and (metric_value(records[rule], metric) or 0.0) < baseline_gate - TOLERANCE
                )
            )
        ),
        "crashed": {rule: records[rule]["crashed"] for rule in rules if records[rule]["crashed"]},
        "wall_seconds": round(time.perf_counter() - start, 1),
    }


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
    card = build_scorecard(
        results,
        native_version=native_version,
        git_sha=git_sha,
        skipped=skipped,
        float_precision=None,
    )
    card["meta"]["cut_env_overrides"] = overrides
    card["meta"]["tolerance"] = TOLERANCE
    card["meta"]["goldenmatch_env"] = {
        k: v for k, v in sorted(os.environ.items()) if k.startswith("GOLDENMATCH_")
    }
    # The sweep process's own seed, and the one every arm's child runs under.
    card["meta"]["pythonhashseed"] = {
        "sweep": os.environ.get("PYTHONHASHSEED"),
        "arms": arm_env()["PYTHONHASHSEED"],
    }

    required = [d.strip() for d in args.require_datasets.split(",") if d.strip()]
    failures: list[str] = []
    for name in required:
        if name not in results:
            failures.append(f"{name}: required dataset not measured")
    for name, record in results.items():
        for arm, rec in record["arms"].items():
            if rec["voided"]:
                failures.append(f"{name}:{arm} voided")
        for arm in ("default", BASELINE_ARM):
            status = record["arms"].get(arm, {}).get("crashed")
            if status:
                failures.append(f"{name}: baseline arm {arm} {status}")
        if record.get("gate_metric") == "labelled":
            baseline_arm = record["arms"].get(BASELINE_ARM, {})
            if not baseline_arm.get("crashed") and baseline_arm.get("labelled") is None:
                failures.append(f"{name}: labelled metric missing on the baseline arm")
        if record.get("routed_matches") is False:
            failures.append(f"{name}: routed arm partition differs from the arm pinning its rule")
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
        float_precision=None,
    )
    merged["meta"]["cut_env_overrides"] = overrides
    merged["meta"]["tolerance"] = TOLERANCE
    merged["meta"]["failures"] = sorted(failures)
    merged["meta"]["missing"] = sorted(set(expected) - set(datasets)) if expected else []
    return merged


def best_rule(record: dict) -> tuple[str, float]:
    """The highest-F1 applied rule arm that did not crash; ties go to the earlier
    ``CUT_RULES`` name. Falls back to the baseline arm (``BASELINE_ARM``) when no
    rule applied -- callers rendering this for humans should treat that sentinel as
    "no rule applied", not as a real rule name."""
    best: tuple[str, float] | None = None
    for rule in CUT_RULES:
        arm = record["arms"][rule]
        if arm.get("crashed"):
            continue
        if arm["applied"] and (best is None or arm["f1"] > best[1]):
            best = (rule, arm["f1"])
    return best or (BASELINE_ARM, record["arms"][BASELINE_ARM]["f1"])


def _not_applied_rules(record: dict) -> list[str]:
    """Rules whose arm ran but neither applied nor voided (e.g. otsu falling back
    on too few training pairs). A crashed arm is reported as crashed instead."""
    return sorted(
        rule
        for rule in CUT_RULES
        if not record["arms"][rule]["applied"]
        and not record["arms"][rule]["voided"]
        and not record["arms"][rule].get("crashed")
    )


def _f1_cell(value: float | None) -> str:
    """An F1 table cell; a crashed baseline has none."""
    return "crashed" if value is None else f"{value:.4f}"


def render_markdown(card: dict, holdout_card: dict | None = None) -> str:
    """The design table (full detail), and -- when ``holdout_card`` is given -- the
    held-out gate view (set-level counts only: no held-out dataset name and no
    held-out F1 anywhere in that section -- the held-out set stays out of view of
    row-writing). Design's missing datasets (``card["meta"]["missing"]``) render as
    an all-MISSING row; a missing held-out dataset is counted, never named."""
    from scripts.autoconfig_quality.corpus import corpus_of

    missing = sorted(card["meta"].get("missing") or [])
    design_missing = [n for n in missing if corpus_of(n) != "holdout"]
    holdout_missing = [n for n in missing if corpus_of(n) == "holdout"]

    lines = [
        "### Design",
        "",
        "| dataset | default F1 | best rule | best F1 | Δ best | below default"
        " | not applied | crashed | reload digest |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(card["datasets"]):
        record = card["datasets"][name]
        baseline = record["arms"][BASELINE_ARM]["f1"]
        rule, f1 = best_rule(record)
        label = "no rule applied" if rule == BASELINE_ARM else rule
        delta = "crashed" if baseline is None or f1 is None else f"{f1 - baseline:+.4f}"
        below = ", ".join(record.get("below_default") or []) or "none"
        not_applied = ", ".join(_not_applied_rules(record)) or "none"
        crashed = (
            ", ".join(f"{r}:{s}" for r, s in sorted((record.get("crashed") or {}).items()))
            or "none"
        )
        match = record.get("reload_partition_match", True)
        digest = "n/a" if match is None else ("same" if match else "DIFFERS")
        lines.append(
            f"| {name} | {_f1_cell(baseline)} | {label} | {_f1_cell(f1)} "
            f"| {delta} | {below} | {not_applied} | {crashed} | {digest} |"
        )
    for name in design_missing:
        lines.append(f"| {name} |" + " MISSING |" * 8)

    out = "\n".join(lines) + "\n"

    if holdout_card is not None:
        n_measured = len(holdout_card["datasets"])
        n_expected = n_measured + len(holdout_missing)
        k_below = sum(
            1 for record in holdout_card["datasets"].values() if record.get("below_default")
        )
        c_crashed = sum(1 for record in holdout_card["datasets"].values() if record.get("crashed"))
        h_lines = [
            "",
            "### Held-out (gate view)",
            "",
            f"Measured {n_measured} of {n_expected} held-out datasets. Datasets with at "
            f"least one rule below default: {k_below}. Datasets with a crashed rule: "
            f"{c_crashed}.",
        ]
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
    if argv and argv[0] == "arm":
        return arm_main(argv[1:])
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
