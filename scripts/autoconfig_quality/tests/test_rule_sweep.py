"""Per-rule link-cut sweep (FS link-cut routing P3)."""

from __future__ import annotations

import json

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.fs_cut_rules import CUT_RULES

from scripts.autoconfig_quality import rule_sweep as S
from scripts.autoconfig_quality.corpus import DESIGN, HOLDOUT


def _cfg() -> GoldenMatchConfig:
    field = MatchkeyField(field="name", scorer="jaro_winkler")
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="fs_a", type="probabilistic", fields=[field]),
            MatchkeyConfig(name="fs_b", type="probabilistic", fields=[field]),
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])]),
    )


def _stats(**per_mk) -> dict:
    return {"fs_link_thresholds": per_mk}


def _entry(cut_rule=None, cut_reason=None) -> dict:
    return {
        "link_threshold": 0.6,
        "source": "evidence_rule",
        "cut_rule": cut_rule,
        "cut_reason": cut_reason,
        "cut_diagnostics": {"midpoint_bits": 2.0},
    }


_SUMMARY = {"f1": 0.9, "precision": 0.95, "recall": 0.85}


def test_arms_run_both_baselines_every_rule_then_the_router():
    assert S.ARMS == ("default", "default_loaded", *CUT_RULES, "routed")


def test_pin_rule_pins_every_probabilistic_matchkey_without_mutating_the_input(tmp_path):
    cfg = _cfg()
    pinned = S.pin_rule(cfg, "evidence_9", tmp_path)
    got = {mk.name: (mk.link_cut_rule, mk.model_path) for mk in pinned.get_matchkeys()}
    assert got == {
        "fs_a": ("evidence_9", str(tmp_path / "fs_a.json")),
        "fs_b": ("evidence_9", str(tmp_path / "fs_b.json")),
    }
    assert all(mk.link_cut_rule is None and mk.model_path is None for mk in cfg.get_matchkeys())
    unset = S.pin_rule(cfg, None, tmp_path)
    assert all(mk.link_cut_rule is None for mk in unset.get_matchkeys())


def test_arm_record_applied_rule():
    rec = S.arm_record(
        "evidence_9",
        _SUMMARY,
        _stats(fs=_entry("evidence_9", "pinned by link_cut_rule")),
        (4, "abc"),
        ["fs"],
    )
    assert rec["applied"] is True and rec["voided"] is False
    assert (rec["f1"], rec["pairs"], rec["digest"]) == (0.9, 4, "abc")
    assert rec["matchkeys"]["fs"]["cut_rule"] == "evidence_9"


def test_arm_record_uncomputable_rule_is_not_applied_but_not_voided():
    reason = "otsu unavailable: needs a training histogram of more than 50 pairs; default rule"
    rec = S.arm_record("otsu", _SUMMARY, _stats(fs=_entry("prior_mid", reason)), (0, "x"), ["fs"])
    assert rec["applied"] is False and rec["voided"] is False


def test_arm_record_overridden_pin_is_voided():
    """KNOWN-POSITIVE for the pinned-arm assertion: an env var that switched on
    posterior scoring voids the arm instead of silently measuring the default."""
    reason = "link_cut_rule=evidence_9 not applied: posterior scoring"
    rec = S.arm_record("evidence_9", _SUMMARY, _stats(fs=_entry(None, reason)), (0, "x"), ["fs"])
    assert rec["applied"] is False and rec["voided"] is True


def test_arm_record_without_a_report_is_voided():
    assert S.arm_record("default", _SUMMARY, {}, (0, "x"), ["fs"])["voided"] is True
    assert (
        S.arm_record(
            "default", _SUMMARY, _stats(fs=_entry("prior_mid", "default rule")), (0, "x"), ["fs"]
        )["voided"]
        is False
    )


def test_arm_record_voided_when_an_expected_matchkey_is_missing_from_the_report():
    """KNOWN-POSITIVE: two probabilistic matchkeys were pinned, but the report only
    carries one -- the other's cut can't be attributed to the pin, so this must not
    read as a clean applied arm."""
    rec = S.arm_record(
        "evidence_9",
        _SUMMARY,
        _stats(fs_a=_entry("evidence_9", "pinned by link_cut_rule")),
        (0, "x"),
        ["fs_a", "fs_b"],
    )
    assert rec["voided"] is True
    assert rec["applied"] is False


def test_cut_env_overrides_reports_only_non_empty_values():
    env = {"GOLDENMATCH_FS_EVIDENCE_CUT": "6", "GOLDENMATCH_FS_CALIBRATED": " ", "PATH": "x"}
    assert S.cut_env_overrides(env) == {"GOLDENMATCH_FS_EVIDENCE_CUT": "6"}


def test_cut_env_vars_refuses_the_signature_prune_variable():
    """KNOWN-POSITIVE: GOLDENMATCH_FS_SIGNATURE_PRUNE passes a pair_filter, which
    stops model_path from saving, so arms would silently retrain instead of
    sharing one model."""
    assert "GOLDENMATCH_FS_SIGNATURE_PRUNE" in S.CUT_ENV_VARS


def test_every_corpus_name_resolves_to_a_loader():
    missing = []
    for name in DESIGN + HOLDOUT:
        try:
            assert callable(S.resolve_loader(name))
        except KeyError:
            missing.append(name)
    assert not missing, missing
    with pytest.raises(KeyError):
        S.resolve_loader("not_a_dataset")


def test_run_refuses_when_a_cut_env_var_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_EVIDENCE_CUT", "6")
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 2
    assert not out.exists()


# ─── watchdog: one capped child process per arm ───────────────────────────────


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


# Tests that exercise another trigger pin ``min_available_mb=0``: the available-memory
# floor reads the whole machine, and a busy laptop can sit below the 1500 MB default.


def _sleeping_child(tmp_path) -> tuple[list[str], str]:
    """A child that writes its interpreter's pid to ``tmp_path / "pid"``, then sleeps
    30 s. Returns ``(argv, marker)``; the marker is in its argv, for cleanup."""
    import sys
    import uuid

    marker = f"gm-arm-test-{uuid.uuid4().hex}"
    code = (
        f"import os, pathlib, time; marker = {marker!r}; "
        f"pathlib.Path({str(tmp_path / 'pid')!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    return [sys.executable, "-c", code], marker


def _kill_marked(marker: str) -> None:
    """Kill every process whose argv carries ``marker`` (cleanup after a kill-path test)."""
    import psutil

    victims = [
        p for p in psutil.process_iter(["cmdline"]) if marker in " ".join(p.info["cmdline"] or [])
    ]
    for p in victims:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=10)


def test_run_arm_classifies_a_child_that_exits_non_zero_as_crashed(tmp_path):
    import sys

    argv = [sys.executable, "-c", "import os; os._exit(137)"]
    rec, payload = S.run_arm(argv, tmp_path / "arm.json", "prior", ["fs"], min_available_mb=0)
    assert rec["crashed"] == "crashed"
    assert rec["returncode"] == 137
    assert rec["kill_reason"] is None
    assert rec["f1"] is None
    assert rec["voided"] is False
    assert rec["applied"] is False
    assert payload is None
    # M4: no pipeline result came back, so no pipeline time; the parent still clocked it.
    assert rec["wall_seconds"] is None
    assert rec["process_seconds"] >= 0.0


def test_run_arm_kills_a_child_over_the_memory_cap(tmp_path):
    import sys
    import time

    pid_file = tmp_path / "pid"
    code = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "b = bytearray(400 * 2**20); time.sleep(30)"
    )
    start = time.perf_counter()
    rec, _ = S.run_arm(
        [sys.executable, "-c", code],
        tmp_path / "arm.json",
        "prior",
        ["fs"],
        memory_cap_mb=150,
        min_available_mb=0,
    )
    assert rec["crashed"] == "memory_cap"
    assert rec["kill_reason"] == "rss_cap"
    assert rec["returncode"] != 0, "a killed arm never reports a clean exit"
    assert time.perf_counter() - start < 20, "the watchdog must kill before the child's sleep ends"
    assert rec["peak_rss_mb"] > 150
    assert not _alive(int(pid_file.read_text()))


def test_run_arm_kills_a_child_past_the_timeout(tmp_path):
    import sys
    import time

    start = time.perf_counter()
    rec, _ = S.run_arm(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path / "arm.json",
        "prior",
        ["fs"],
        timeout_s=1,
        min_available_mb=0,
    )
    assert rec["crashed"] == "timeout"
    assert rec["kill_reason"] is None
    assert rec["returncode"] != 0, "a killed arm never reports a clean exit"
    assert rec["f1"] is None
    assert rec["process_seconds"] >= 1.0
    assert time.perf_counter() - start < 20


def test_run_arm_kills_a_child_when_machine_available_memory_drops_below_the_floor(
    tmp_path, monkeypatch
):
    """I2: the child's RSS stays tiny, but the machine runs out -- the floor fires."""
    import time
    from types import SimpleNamespace

    import psutil

    pid_file = tmp_path / "pid"

    def low_once_the_child_runs():
        mb = 100 if pid_file.exists() else 10**6
        return SimpleNamespace(available=mb * 2**20)

    monkeypatch.setattr(psutil, "virtual_memory", low_once_the_child_runs)
    argv, marker = _sleeping_child(tmp_path)
    start = time.perf_counter()
    try:
        rec, _ = S.run_arm(
            argv, tmp_path / "arm.json", "prior", ["fs"], memory_cap_mb=10**6, min_available_mb=1500
        )
    finally:
        _kill_marked(marker)  # no-op when the watchdog did its job
    assert rec["crashed"] == "memory_cap"
    assert rec["kill_reason"] == "available_floor"
    assert time.perf_counter() - start < 20
    assert not _alive(int(pid_file.read_text()))


def test_run_arm_leaves_the_killed_root_to_popen_and_bounds_every_wait(tmp_path, monkeypatch):
    """M1 + M2: psutil must not reap the direct child (on Linux Popen then reports
    returncode 0 for a killed arm), and no wait after a kill may block forever."""
    import subprocess

    import psutil

    waited_by_psutil: list[int] = []
    popen_pids: set[int] = set()
    popen_timeouts: list[float | None] = []
    real_wait_procs = psutil.wait_procs
    real_popen_wait = subprocess.Popen.wait

    def wait_procs(procs, *args, **kwargs):
        waited_by_psutil.extend(p.pid for p in procs)
        return real_wait_procs(procs, *args, **kwargs)

    def popen_wait(self, timeout=None):
        popen_pids.add(self.pid)
        popen_timeouts.append(timeout)
        return real_popen_wait(self, timeout)

    monkeypatch.setattr(psutil, "wait_procs", wait_procs)
    monkeypatch.setattr(subprocess.Popen, "wait", popen_wait)
    argv, marker = _sleeping_child(tmp_path)
    try:
        rec, _ = S.run_arm(
            argv, tmp_path / "arm.json", "prior", ["fs"], timeout_s=1, min_available_mb=0
        )
    finally:
        monkeypatch.undo()
        _kill_marked(marker)
    assert rec["crashed"] == "timeout"
    assert rec["returncode"] != 0
    assert popen_pids and not popen_pids & set(waited_by_psutil)
    assert popen_timeouts and None not in popen_timeouts


def test_run_arm_kill_path_cannot_mask_the_original_error(tmp_path, monkeypatch):
    """M2: an AccessDenied while cleaning up must not replace the error that caused it."""
    import psutil

    def boom(proc):
        raise RuntimeError("original")

    def denied(self, recursive=False):
        raise psutil.AccessDenied(self.pid)

    monkeypatch.setattr(S, "_tree_rss_mb", boom)
    monkeypatch.setattr(psutil.Process, "children", denied)
    argv, marker = _sleeping_child(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="original"):
            S.run_arm(argv, tmp_path / "arm.json", "prior", ["fs"], min_available_mb=0)
    finally:
        monkeypatch.undo()
        _kill_marked(marker)


def test_execute_arm_applies_every_rule_on_one_saved_model(tmp_path, monkeypatch):
    """Every arm through the one function the child process runs, in-process, on a
    small frame: no arm voided, every computable rule applied, one saved model."""
    for var in S.CUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    from scripts.autoconfig_quality.anchors import gen_labeled

    df, gt = gen_labeled(n_entities=60, seed=7)
    cfg = auto_configure_probabilistic_df(df)
    matchkeys = sorted(mk.name for mk in cfg.get_matchkeys() if mk.type == "probabilistic")
    assert matchkeys, "must auto-configure at least one probabilistic matchkey"

    records, payloads = {}, {}
    for arm in S.ARMS:
        payload = json.loads(json.dumps(S.execute_arm(df, gt, cfg, arm, tmp_path)))
        payloads[arm] = payload
        records[arm] = S.arm_record(
            arm,
            payload["summary"],
            {"fs_link_thresholds": payload["report"]},
            tuple(payload["partition"]),
            matchkeys,
        )
        assert payload["wall_seconds"] >= 0.0

    assert not [a for a, rec in records.items() if rec["voided"]]
    not_applied = [r for r in CUT_RULES if r != "otsu" and not records[r]["applied"]]
    assert not not_applied, not_applied
    assert sorted(p.name for p in tmp_path.glob("*.json")) == sorted(f"{n}.json" for n in matchkeys)
    report = payloads[S.BASELINE_ARM]["report"]
    assert all(
        e["cut_diagnostics"] and "midpoint_bits" in e["cut_diagnostics"] for e in report.values()
    )


def test_sweep_dataset_runs_each_arm_in_its_own_child_on_one_saved_model(tmp_path, monkeypatch):
    """End-to-end isolation: real child processes, one config file, one EM model."""
    for var in S.CUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    # The floor reads the whole machine; this test is about isolation, not pressure.
    monkeypatch.setenv("GOLDENMATCH_CUT_RULES_MIN_AVAILABLE_MB", "0")
    monkeypatch.delenv("GOLDENMATCH_CUT_RULES_DATASET_BUDGET_S", raising=False)
    arms = ("default", "default_loaded", "evidence_9", "routed")
    out = S.sweep_dataset("person", tmp_path, arms=arms)

    assert (tmp_path / "person" / "config.json").is_file()
    assert set(out["arms"]) == set(arms)
    assert all(rec["crashed"] is None for rec in out["arms"].values())
    assert all(rec["peak_rss_mb"] > 0 for rec in out["arms"].values())
    # M4: the parent's clock spans interpreter start-up, so it covers the pipeline time.
    assert all(rec["process_seconds"] >= rec["wall_seconds"] for rec in out["arms"].values())
    assert not [a for a, rec in out["arms"].items() if rec["voided"]]
    assert out["arms"]["evidence_9"]["applied"] is True
    assert out["crashed"] == {}
    assert set(out["below_default"]) <= {"evidence_9"}
    # I1: one shared EM model per dataset, enforced (not just recorded).
    assert out["probabilistic_matchkeys"], "must record the auto-configured matchkey names"
    assert out["models_saved"], "the default arm must save the EM model the other arms load"
    assert sorted(out["models_saved"]) == sorted(
        f"{n}.json" for n in out["probabilistic_matchkeys"]
    )
    assert out["model_complete"] is True
    assert out["model_stable"] is True
    assert all(d and "midpoint_bits" in d for d in out["cut_diagnostics"].values())
    assert isinstance(out["model_reload_delta"], float)
    assert out["reload_partition_match"] in (True, False)
    assert isinstance(out["wall_seconds"], float)
    assert out["corpus"] == "design"
    assert out["gate_metric"] == "f1"
    assert all(rec["labelled"] is None for rec in out["arms"].values())
    # The routed arm loads the same model with the router on. Where no shipped row fired, it
    # must reproduce the baseline partition exactly.
    if all(rule is None for rule in out["routed_rules"].values()):
        assert out["routed_matches"] is True
    else:
        assert out["routed_matches"] in (True, None)


# ─── the routed arm (P4) ──────────────────────────────────────────────────────


def test_cut_env_vars_include_the_router():
    assert "GOLDENMATCH_FS_CUT_ROUTER" in S.CUT_ENV_VARS


def test_arm_env_turns_the_router_on_for_the_routed_arm_only():
    assert S.arm_env("routed")["GOLDENMATCH_FS_CUT_ROUTER"] == "on"
    for arm in ("default", "default_loaded", *CUT_RULES):
        assert S.arm_env(arm)["GOLDENMATCH_FS_CUT_ROUTER"] == "off"


def test_the_routed_arm_pins_nothing_and_applies_whatever_the_router_chose():
    assert S.pinned_rule("routed") is None
    assert S.pinned_rule("default_loaded") is None
    assert S.pinned_rule("evidence_5") == "evidence_5"
    rec = S.arm_record(
        "routed", _SUMMARY, _stats(fs=_entry("evidence_5", "routed by wide: x")), (1, "d"), ["fs"]
    )
    assert (rec["applied"], rec["voided"]) == (True, False)


def _routing_arms(routed_matchkeys: dict, digests: dict | None = None, **overrides) -> dict:
    digests = digests or {}
    arms = {
        arm: {"digest": digests.get(arm, "base"), "voided": False, "crashed": None, "matchkeys": {}}
        for arm in S.ARMS
    }
    arms["routed"]["matchkeys"] = routed_matchkeys
    for arm, fields in overrides.items():
        arms[arm].update(fields)
    return arms


def test_routed_check_compares_an_unrouted_arm_with_the_baseline():
    arms = _routing_arms({"fs": _entry("prior_mid", "default rule")})
    assert S.routed_check(arms) == ({"fs": None}, True)


def test_routed_check_compares_a_routed_arm_with_the_arm_pinning_its_rule():
    entry = {"fs": _entry("evidence_5", "routed by wide: many fields")}
    same = _routing_arms(entry, {"routed": "e5", "evidence_5": "e5"})
    assert S.routed_check(same) == ({"fs": "evidence_5"}, True)
    differs = _routing_arms(entry, {"routed": "e5", "evidence_5": "other"})
    assert S.routed_check(differs) == ({"fs": "evidence_5"}, False)


def test_routed_check_has_nothing_to_compare_for_mixed_routes_or_a_crash():
    mixed = _routing_arms(
        {
            "fs_a": _entry("evidence_5", "routed by wide: x"),
            "fs_b": _entry("prior_mid", "default rule"),
        }
    )
    assert S.routed_check(mixed) == ({"fs_a": "evidence_5", "fs_b": None}, None)
    crashed = _routing_arms({}, routed={"crashed": "timeout"})
    assert S.routed_check(crashed) == ({}, None)
    target_crashed = _routing_arms(
        {"fs": _entry("evidence_5", "routed by wide: x")}, evidence_5={"crashed": "memory_cap"}
    )
    assert S.routed_check(target_crashed) == ({"fs": "evidence_5"}, None)


def test_sweep_dataset_turns_the_router_on_in_the_routed_arm_child_only(tmp_path, monkeypatch):
    _small_person(monkeypatch)
    seen: dict[str, str] = {}
    instant = _fake_run_arm({})

    def spy(argv, out_path, arm, expected_matchkeys, **kwargs):
        seen[arm] = kwargs["env"]["GOLDENMATCH_FS_CUT_ROUTER"]
        return instant(argv, out_path, arm, expected_matchkeys, **kwargs)

    monkeypatch.setattr(S, "run_arm", spy)
    rec = S.sweep_dataset("person", tmp_path)
    assert seen == {arm: ("on" if arm == "routed" else "off") for arm in S.ARMS}
    assert rec["routed_rules"] and all(v is None for v in rec["routed_rules"].values())
    assert rec["routed_matches"] is True


def test_run_reports_failure_when_the_routed_arm_disagrees(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("design", {})
        rec["routed_matches"] = False
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert (
        "person: routed arm partition differs from the arm pinning its rule"
        in card["meta"]["failures"]
    )


# ─── run() metadata and failure paths (I1 + I2 + I5) ──────────────────────────


def _clear_cut_env(monkeypatch):
    for var in S.CUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _record(
    corpus: str,
    f1_by_arm: dict,
    applied: dict | None = None,
    voided: dict | None = None,
    *,
    model_complete: bool = True,
    model_stable: bool = True,
    reload_partition_match: bool = True,
) -> dict:
    applied = applied or {}
    voided = voided or {}
    arms = {
        arm: {
            "f1": f1_by_arm.get(arm, 0.5),
            "precision": 0.0,
            "recall": 0.0,
            "pairs": 0,
            "digest": "d",
            "matchkeys": {},
            "applied": applied.get(arm, True),
            "voided": voided.get(arm, False),
            "crashed": None,
            "peak_rss_mb": 10.0,
            "wall_seconds": 0.1,
            "labelled": None,
        }
        for arm in S.ARMS
    }
    return {
        "corpus": corpus,
        "rows": 10,
        "gt_pairs": 3,
        "probabilistic_matchkeys": ["fs"],
        "arms": arms,
        "cut_diagnostics": {},
        "gate_metric": "f1",
        "routed_rules": {},
        "routed_matches": None,
        "models_saved": ["fs.json"],
        "model_complete": model_complete,
        "model_stable": model_stable,
        "reload_partition_match": reload_partition_match,
        "model_reload_delta": 0.0,
        "below_default": ["midpoint"],
        "crashed": {},
        "wall_seconds": 1.0,
    }


def _card(sha: str, *, failures: list[str] | None = None, **datasets) -> dict:
    return {
        "meta": {
            "git_sha": sha,
            "native_version": "0.2.2",
            "datasets_run": sorted(datasets),
            "datasets_skipped": {},
            "cut_env_overrides": {},
            "tolerance": 0.01,
            "failures": failures or [],
        },
        "datasets": datasets,
    }


def test_run_records_goldenmatch_env_and_pythonhashseed_in_meta(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    monkeypatch.setattr(S, "sweep_dataset", lambda name, work_dir: _record("design", {}))
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 0
    card = json.loads(out.read_text())
    assert card["meta"]["goldenmatch_env"].get("GOLDENMATCH_NATIVE") == "0"
    # I3: the sweep's own seed, and the seed every arm's child actually ran under.
    assert card["meta"]["pythonhashseed"] == {"sweep": "7", "arms": "0"}


def test_run_reports_failure_when_an_arm_is_voided(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("design", {})
        rec["arms"]["prior_mid"]["voided"] = True
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert "person:prior_mid voided" in card["meta"]["failures"]


def test_run_reports_failure_when_a_required_dataset_is_not_measured(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setattr(S, "sweep_dataset", lambda name, work_dir: None)
    out = tmp_path / "card.json"
    rc = S.run(["--datasets", "person", "--require-datasets", "person", "--out", str(out)])
    assert rc == 1
    card = json.loads(out.read_text())
    assert any("person" in f for f in card["meta"]["failures"])


def test_run_reports_failure_when_zero_datasets_are_measured(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setattr(S, "sweep_dataset", lambda name, work_dir: None)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert "0 datasets measured" in card["meta"]["failures"]


def test_run_reports_failure_when_model_not_complete(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setattr(
        S, "sweep_dataset", lambda name, work_dir: _record("design", {}, model_complete=False)
    )
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert any(
        "shared EM model not saved for every probabilistic matchkey" in f
        for f in card["meta"]["failures"]
    )


def test_run_reports_failure_when_model_not_stable(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setattr(
        S, "sweep_dataset", lambda name, work_dir: _record("design", {}, model_stable=False)
    )
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert any("shared EM model changed between arms" in f for f in card["meta"]["failures"])


def test_run_returns_zero_with_no_failures_for_a_clean_fake_record(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    monkeypatch.setattr(S, "sweep_dataset", lambda name, work_dir: _record("design", {}))
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 0
    card = json.loads(out.read_text())
    assert card["meta"]["failures"] == []


# ─── crashed arms: a rule crash is a result, a baseline crash is a failure ────


def _small_person(monkeypatch):
    """``person`` resolves to a 60-entity frame, so the parent's auto-configure is fast."""
    from scripts.autoconfig_quality.anchors import gen_labeled

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setattr(S, "resolve_loader", lambda name: lambda: gen_labeled(60, seed=7))


def _fake_run_arm(crash: dict[str, str], labelled: dict[str, float] | None = None):
    """An arm runner that runs nothing: every arm applies at ``_SUMMARY``'s F1, except the
    arms in ``crash``, which come back killed with that status. ``labelled`` gives an arm a
    labelled-pairs F1."""

    def fake(argv, out_path, arm, expected_matchkeys, **kwargs):
        if arm in crash:
            return S.killed_record(crash[arm], 1, 20.0, 0.1), None
        rule = S.pinned_rule(arm)
        reason = "pinned by link_cut_rule" if rule else "default rule"
        report = {mk: _entry(rule, reason) for mk in expected_matchkeys}
        lab = None
        if labelled and arm in labelled:
            value = labelled[arm]
            lab = {"f1": value, "precision": value, "recall": value, "labelled_pairs": 4}
        rec = S.arm_record(arm, _SUMMARY, _stats(**report), (1, "d"), expected_matchkeys)
        rec.update(
            crashed=None, peak_rss_mb=20.0, wall_seconds=0.1, process_seconds=0.2, labelled=lab
        )
        payload = {
            "summary": _SUMMARY,
            "report": report,
            "partition": [1, "d"],
            "wall_seconds": 0.1,
            "labelled": lab,
        }
        return rec, payload

    return fake


def _cells(md: str, name: str) -> list[str]:
    row = next(line for line in md.splitlines() if line.startswith(f"| {name} |"))
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def test_a_crashed_rule_arm_counts_below_default_and_is_reported(tmp_path, monkeypatch):
    _small_person(monkeypatch)
    monkeypatch.setattr(S, "run_arm", _fake_run_arm({"prior": "memory_cap"}))
    rec = S.sweep_dataset("person", tmp_path)

    assert rec["arms"]["prior"]["crashed"] == "memory_cap"
    assert rec["crashed"] == {"prior": "memory_cap"}
    assert rec["below_default"] == ["prior"]
    assert isinstance(rec["model_reload_delta"], float)
    assert S.best_rule(rec)[0] not in ("prior", S.BASELINE_ARM)

    design = S.render_markdown(S.merge_cards([_card("abc", person=rec)]))
    cells = _cells(design, "person")
    assert cells[7] == "prior:memory_cap"
    assert "prior" not in cells[6], "a crashed arm is crashed, not 'not applied'"

    holdout_card = S.merge_cards([_card("abc", abt_buy={**rec, "corpus": "holdout"})])
    gate = S.render_markdown({"meta": holdout_card["meta"], "datasets": {}}, holdout_card)
    assert (
        "Measured 1 of 1 held-out datasets. Datasets with at least one rule below "
        "default: 1. Datasets with a crashed rule: 1." in gate
    )
    held_out_section = gate.split("### Held-out")[1]
    assert "prior" not in held_out_section
    assert "abt_buy" not in held_out_section
    assert "0.9000" not in held_out_section


def test_a_crashed_baseline_arm_fails_the_dataset(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)
    _small_person(monkeypatch)
    monkeypatch.setattr(S, "run_arm", _fake_run_arm({"default_loaded": "crashed"}))
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 1

    card = json.loads(out.read_text())
    assert "person: baseline arm default_loaded crashed" in card["meta"]["failures"]
    rec = card["datasets"]["person"]
    assert rec["below_default"] == []
    assert rec["model_reload_delta"] is None
    assert rec["reload_partition_match"] is None
    # The matrix still renders a dataset whose baseline has no F1.
    assert _cells(S.render_markdown(card), "person")[1] == "crashed"


def test_dataset_budget_times_out_the_arms_it_cannot_fit_without_starting_them(
    tmp_path, monkeypatch
):
    """I1: every arm here needs 0.3 s against a 0.5 s dataset budget, so two arms
    run and the rest are recorded as timed out, never started."""
    import time

    _small_person(monkeypatch)
    started: list[str] = []
    timeouts: list[float] = []
    instant = _fake_run_arm({})

    def slow(argv, out_path, arm, expected_matchkeys, **kwargs):
        started.append(arm)
        timeouts.append(kwargs["timeout_s"])
        time.sleep(0.3)
        return instant(argv, out_path, arm, expected_matchkeys, **kwargs)

    monkeypatch.setattr(S, "run_arm", slow)
    arms = ("default", "default_loaded", "evidence_9", "prior")
    rec = S.sweep_dataset("person", tmp_path, arms=arms, budget_s=0.5)

    assert started == ["default", "default_loaded"]
    assert timeouts[0] <= 0.5 / len(arms), "each arm gets at most its share of what is left"
    shape = set(S.killed_record("timeout", None, 0.0, 0.0))
    for arm in ("evidence_9", "prior"):
        assert rec["arms"][arm]["crashed"] == "timeout"
        assert rec["arms"][arm]["kill_reason"] == "dataset_budget"
        assert rec["arms"][arm]["returncode"] is None
        assert set(rec["arms"][arm]) == shape
    assert rec["below_default"] == ["evidence_9", "prior"]


def test_arm_log_line_omits_f1_for_holdout_datasets(tmp_path, monkeypatch, capsys):
    """M3: per-rule F1 on a held-out dataset must not reach the CI log."""
    _small_person(monkeypatch)
    monkeypatch.setattr(S, "run_arm", _fake_run_arm({}))
    arms = ("default", "default_loaded", "evidence_9")

    S.sweep_dataset("abt_buy", tmp_path, arms=arms)
    holdout = [
        ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[cut-rules] abt_buy:")
    ]
    S.sweep_dataset("person", tmp_path, arms=arms)
    design = [
        ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[cut-rules] person:")
    ]

    assert len(holdout) == len(arms) and not [ln for ln in holdout if "f1" in ln]
    assert len(design) == len(arms) and all("f1=0.9" in ln for ln in design)


# ─── labelled-pairs metric (P4) ───────────────────────────────────────────────


def test_labelled_summary_scores_only_labelled_pairs():
    clusters = {1: {"members": [0, 1, 2]}, 2: {"members": [3]}, 3: {"members": [4, 5]}}
    labels = {(0, 1): True, (0, 2): False, (3, 4): True, (4, 5): False}
    # (0,1) match together: TP. (0,2) non-match together: FP. (3,4) match apart: FN.
    # (4,5) non-match together: FP. The unlabelled (1,2) counts for nothing.
    assert S.labelled_summary(clusters, labels) == {
        "f1": 0.4,
        "precision": 0.3333,
        "recall": 0.5,
        "labelled_pairs": 4,
    }


def test_execute_arm_adds_the_labelled_metric_only_when_labels_are_given(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import goldenmatch

    clusters = {1: {"members": [0, 1]}, 2: {"members": [2]}}
    monkeypatch.setattr(
        goldenmatch, "dedupe_df", lambda df, config: SimpleNamespace(clusters=clusters, stats={})
    )
    plain = S.execute_arm(None, {(0, 1)}, _cfg(), "default_loaded", tmp_path)
    labelled = S.execute_arm(
        None, {(0, 1)}, _cfg(), "default_loaded", tmp_path, labels={(0, 1): True, (1, 2): False}
    )
    assert plain["labelled"] is None
    assert labelled["labelled"] == {
        "f1": 1.0,
        "precision": 1.0,
        "recall": 1.0,
        "labelled_pairs": 2,
    }
    assert labelled["summary"] == plain["summary"]


def test_gate_metric_is_labelled_for_magellan_datasets_only():
    assert S.gate_metric_of("walmart_amazon") == "labelled"
    assert S.gate_metric_of("fodors_zagats") == "labelled"
    assert S.gate_metric_of("abt_buy") == "f1"
    assert S.metric_value({"f1": 0.9, "labelled": {"f1": 0.7}}, "labelled") == 0.7
    assert S.metric_value({"f1": 0.9, "labelled": None}, "labelled") is None
    assert S.metric_value({"f1": 0.9, "labelled": {"f1": 0.7}}, "f1") == 0.9


def test_sweep_dataset_gates_magellan_datasets_on_the_labelled_metric(tmp_path, monkeypatch):
    _small_person(monkeypatch)
    labelled = {"default": 0.8, "default_loaded": 0.8, "evidence_9": 0.7}
    monkeypatch.setattr(S, "run_arm", _fake_run_arm({}, labelled=labelled))
    arms = ("default", "default_loaded", "evidence_9")

    magellan = S.sweep_dataset("walmart_amazon", tmp_path, arms=arms)
    plain = S.sweep_dataset("person", tmp_path, arms=arms)

    assert magellan["gate_metric"] == "labelled"
    assert magellan["below_default"] == ["evidence_9"]
    assert plain["gate_metric"] == "f1"
    assert plain["below_default"] == []


def test_run_reports_failure_when_the_labelled_metric_is_missing(tmp_path, monkeypatch):
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("holdout", {})
        rec["gate_metric"] = "labelled"
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "walmart_amazon", "--out", str(out)]) == 1
    card = json.loads(out.read_text())
    assert "walmart_amazon: labelled metric missing on the baseline arm" in card["meta"]["failures"]


# ─── best_rule / merge_cards / render_markdown / merge CLI ────────────────────


def test_best_rule_takes_the_highest_applied_rule_with_ties_in_rule_order():
    rec = _record(
        "design",
        {"default_loaded": 0.8, "prior": 0.9, "evidence_9": 0.9, "otsu": 0.99},
        applied={"otsu": False},
    )
    assert S.best_rule(rec) == ("prior", 0.9)


def test_best_rule_falls_back_to_the_baseline_when_nothing_applied():
    rec = _record("design", {"default_loaded": 0.7}, applied={r: False for r in CUT_RULES})
    assert S.best_rule(rec) == ("default_loaded", 0.7)


def test_merge_cards_combines_datasets_from_one_commit():
    merged = S.merge_cards(
        [
            _card("abc", febrl3=_record("design", {})),
            _card("abc", abt_buy=_record("holdout", {})),
        ]
    )
    assert sorted(merged["datasets"]) == ["abt_buy", "febrl3"]
    assert merged["meta"]["git_sha"] == "abc"
    assert merged["meta"]["tolerance"] == 0.01
    assert merged["meta"]["missing"] == []


def test_merge_cards_refuses_mixed_commits_and_duplicates():
    with pytest.raises(ValueError, match="different commits"):
        S.merge_cards(
            [_card("abc", a=_record("design", {})), _card("def", b=_record("design", {}))]
        )
    with pytest.raises(ValueError, match="two sweep results"):
        S.merge_cards(
            [_card("abc", a=_record("design", {})), _card("abc", a=_record("design", {}))]
        )


def test_merge_cards_empty_raises():
    with pytest.raises(ValueError, match="no sweep results to merge"):
        S.merge_cards([])


def test_merge_cards_carries_failures_and_computes_missing():
    merged = S.merge_cards(
        [
            _card("abc", febrl3=_record("design", {}), failures=["febrl3:otsu voided"]),
        ],
        expected=["febrl3", "dblp_acm", "walmart_amazon"],
    )
    assert merged["meta"]["failures"] == ["febrl3:otsu voided"]
    assert merged["meta"]["missing"] == ["dblp_acm", "walmart_amazon"]


def test_render_markdown_design_table_then_holdout_gate_view():
    full = S.merge_cards(
        [
            _card(
                "abc",
                febrl3=_record("design", {"default_loaded": 0.9, "prior_mid": 0.95}),
                abt_buy=_record("holdout", {"default_loaded": 0.8, "evidence_12": 0.85}),
            )
        ]
    )
    design_card, holdout_card = S._split_by_corpus(full)
    md = S.render_markdown(design_card, holdout_card)
    assert "### Design" in md
    assert "### Held-out (gate view)" in md
    lines = md.splitlines()
    assert (
        "| febrl3 | 0.9000 | prior_mid | 0.9500 | +0.0500 | midpoint | none | none | same |"
        in lines
    )
    held_out_section = md.split("### Held-out")[1]
    assert (
        "Measured 1 of 1 held-out datasets. Datasets with at least one rule below "
        "default: 1. Datasets with a crashed rule: 0." in md
    )
    # gate view is counts only: no dataset names, no rule names, no per-dataset F1.
    assert "abt_buy" not in held_out_section
    assert "evidence_12" not in held_out_section
    assert "0.8000" not in held_out_section


def test_render_markdown_shows_no_rule_applied_when_nothing_applied():
    rec = _record("design", {"default_loaded": 0.7}, applied={r: False for r in CUT_RULES})
    card = S.merge_cards([_card("abc", x=rec)])
    md = S.render_markdown(card)
    assert "| x | 0.7000 | no rule applied | 0.7000 | +0.0000 |" in md


def test_render_markdown_not_applied_column_excludes_voided_rules():
    rec = _record(
        "design",
        {"default_loaded": 0.7},
        applied={"otsu": False, "evidence_9": False},
        voided={"evidence_9": True},
    )
    card = S.merge_cards([_card("abc", x=rec)])
    md = S.render_markdown(card)
    row = next(line for line in md.splitlines() if line.startswith("| x |"))
    assert "otsu" in row
    assert "evidence_9" not in row


def test_render_markdown_reload_digest_differs_when_partition_mismatches():
    rec = _record("design", {"default_loaded": 0.6}, reload_partition_match=False)
    card = S.merge_cards([_card("abc", y=rec)])
    md = S.render_markdown(card)
    row = next(line for line in md.splitlines() if line.startswith("| y |"))
    assert row.endswith("| DIFFERS |")


def test_render_markdown_missing_rows_land_in_the_matching_corpus_table():
    full = S.merge_cards(
        [_card("abc", febrl3=_record("design", {}))],
        expected=["febrl3", "dblp_acm", "walmart_amazon"],
    )
    design_card, holdout_card = S._split_by_corpus(full)
    md = S.render_markdown(design_card, holdout_card)
    assert "| dblp_acm |" + " MISSING |" * 8 in md
    # A missing held-out dataset is counted in M, never named or rendered as a row.
    assert "walmart_amazon" not in md
    held_out_section = md.split("### Held-out")[1]
    assert (
        "Measured 0 of 1 held-out datasets. Datasets with at least one rule below "
        "default: 0. Datasets with a crashed rule: 0." in held_out_section
    )


def test_render_markdown_holdout_counts_below_default_and_crashed_without_naming():
    """A held-out dataset with a below-default rule AND a crash is counted in both
    K and C, but neither it nor its rule is ever named in the held-out section."""
    clean = _record("holdout", {})
    clean["below_default"] = []
    troubled = _record("holdout", {})
    troubled["below_default"] = ["prior"]
    troubled["crashed"] = {"prior": "memory_cap"}
    full = S.merge_cards(
        [
            _card(
                "abc",
                febrl3=_record("design", {}),
                abt_buy=clean,
                walmart_amazon=troubled,
            )
        ]
    )
    design_card, holdout_card = S._split_by_corpus(full)
    md = S.render_markdown(design_card, holdout_card)
    held_out_section = md.split("### Held-out")[1]
    assert (
        "Measured 2 of 2 held-out datasets. Datasets with at least one rule below "
        "default: 1. Datasets with a crashed rule: 1." in held_out_section
    )
    assert "abt_buy" not in held_out_section
    assert "walmart_amazon" not in held_out_section
    assert "prior" not in held_out_section


def test_render_markdown_renders_a_failures_section():
    card = S.merge_cards([_card("abc", x=_record("design", {}), failures=["x:otsu voided"])])
    md = S.render_markdown(card)
    assert "**Failures**" in md
    assert "- x:otsu voided" in md


def test_merge_cli_writes_json_and_markdown_split_by_corpus(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_card("abc", febrl3=_record("design", {}))))
    b.write_text(json.dumps(_card("abc", abt_buy=_record("holdout", {}))))
    out, hout, md = tmp_path / "m.json", tmp_path / "mh.json", tmp_path / "m.md"
    rc = S.main(
        [
            "merge",
            str(a),
            str(b),
            "--out",
            str(out),
            "--holdout-out",
            str(hout),
            "--summary-md",
            str(md),
        ]
    )
    assert rc == 0
    design = json.loads(out.read_text())
    holdout = json.loads(hout.read_text())
    assert sorted(design["datasets"]) == ["febrl3"]
    assert sorted(holdout["datasets"]) == ["abt_buy"]
    assert "| febrl3 |" in md.read_text()


def test_merge_cli_exits_2_when_holdout_present_without_holdout_out(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_card("abc", abt_buy=_record("holdout", {}))))
    out = tmp_path / "m.json"
    rc = S.main(["merge", str(a), "--out", str(out)])
    assert rc == 2
    assert not out.exists()


def test_merge_cli_returns_1_and_still_writes_outputs_when_a_card_carries_failures(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(
        json.dumps(_card("abc", febrl3=_record("design", {}), failures=["febrl3:otsu voided"]))
    )
    out, md = tmp_path / "m.json", tmp_path / "m.md"
    rc = S.main(["merge", str(a), "--out", str(out), "--summary-md", str(md)])
    assert rc == 1
    assert out.exists()
    assert md.exists()
    assert "febrl3:otsu voided" in json.loads(out.read_text())["meta"]["failures"]


def test_merge_cli_expect_json_reports_missing_and_returns_1(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_card("abc", febrl3=_record("design", {}))))
    out = tmp_path / "m.json"
    rc = S.main(["merge", str(a), "--out", str(out), "--expect-json", '["febrl3", "dblp_acm"]'])
    assert rc == 1
    assert json.loads(out.read_text())["meta"]["missing"] == ["dblp_acm"]


def test_merge_cli_holdout_gate_view_hides_rule_names(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(
        json.dumps(
            _card(
                "abc",
                febrl3=_record("design", {"default_loaded": 0.9, "prior_mid": 0.95}),
                abt_buy=_record("holdout", {"default_loaded": 0.8, "evidence_12": 0.85}),
            )
        )
    )
    out, hout, md = tmp_path / "m.json", tmp_path / "mh.json", tmp_path / "m.md"
    rc = S.main(
        [
            "merge",
            str(a),
            "--out",
            str(out),
            "--holdout-out",
            str(hout),
            "--summary-md",
            str(md),
        ]
    )
    assert rc == 0
    text = md.read_text()
    assert "prior_mid" in text
    held_out_section = text.split("### Held-out")[1]
    assert "evidence_12" not in held_out_section
    assert "abt_buy" not in held_out_section
    assert "0.8000" not in held_out_section
    assert (
        "Measured 1 of 1 held-out datasets. Datasets with at least one rule below "
        "default: 1. Datasets with a crashed rule: 0." in held_out_section
    )


def test_sweep_scorecards_keep_cut_diagnostics_exact(tmp_path, monkeypatch):
    """P5: the gate replays recorded diagnostics against row boundaries; rounding to 6 dp could
    route a boundary dataset differently from the live router."""
    _clear_cut_env(monkeypatch)

    def fake(name, work_dir):
        rec = _record("design", {})
        rec["cut_diagnostics"] = {"fs": {"midpoint_bits": 2.1234567891234}}
        return rec

    monkeypatch.setattr(S, "sweep_dataset", fake)
    out = tmp_path / "card.json"
    assert S.run(["--datasets", "person", "--out", str(out)]) == 0
    card = json.loads(out.read_text())
    assert card["datasets"]["person"]["cut_diagnostics"]["fs"]["midpoint_bits"] == 2.1234567891234
    merged = S.merge_cards([card])
    assert merged["datasets"]["person"]["cut_diagnostics"]["fs"]["midpoint_bits"] == 2.1234567891234
