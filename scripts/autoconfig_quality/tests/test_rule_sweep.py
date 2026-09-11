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


def test_arms_run_both_baselines_then_every_rule_once():
    assert S.ARMS == ("default", "default_loaded", *CUT_RULES)
    assert len(set(S.ARMS)) == len(S.ARMS)
    assert S.BASELINE_ARM == "default_loaded"


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


def test_run_arm_classifies_a_child_that_exits_non_zero_as_crashed(tmp_path):
    import sys

    argv = [sys.executable, "-c", "import os; os._exit(137)"]
    rec, payload = S.run_arm(argv, tmp_path / "arm.json", "prior", ["fs"])
    assert rec["crashed"] == "crashed"
    assert rec["returncode"] == 137
    assert rec["f1"] is None
    assert rec["voided"] is False
    assert rec["applied"] is False
    assert payload is None


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
        [sys.executable, "-c", code], tmp_path / "arm.json", "prior", ["fs"], memory_cap_mb=150
    )
    assert rec["crashed"] == "memory_cap"
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
    )
    assert rec["crashed"] == "timeout"
    assert rec["f1"] is None
    assert time.perf_counter() - start < 20


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
    arms = ("default", "default_loaded", "evidence_9")
    out = S.sweep_dataset("person", tmp_path, arms=arms)

    assert (tmp_path / "person" / "config.json").is_file()
    assert set(out["arms"]) == set(arms)
    assert all(rec["crashed"] is None for rec in out["arms"].values())
    assert all(rec["peak_rss_mb"] > 0 for rec in out["arms"].values())
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
    assert card["meta"]["pythonhashseed"] == "7"


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


def _fake_run_arm(crash: dict[str, str]):
    """An arm runner that runs nothing: every arm applies at ``_SUMMARY``'s F1,
    except the arms in ``crash``, which come back killed with that status."""

    def fake(argv, out_path, arm, expected_matchkeys, **kwargs):
        if arm in crash:
            return S.killed_record(crash[arm], 1, 20.0, 0.1), None
        rule = None if arm in ("default", S.BASELINE_ARM) else arm
        report = {mk: _entry(rule, "pinned by link_cut_rule") for mk in expected_matchkeys}
        rec = S.arm_record(arm, _SUMMARY, _stats(**report), (1, "d"), expected_matchkeys)
        rec.update(crashed=None, peak_rss_mb=20.0, wall_seconds=0.1)
        payload = {
            "summary": _SUMMARY,
            "report": report,
            "partition": [1, "d"],
            "wall_seconds": 0.1,
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
    assert "| abt_buy | 0.9000 | 1 | 0 | 1 |" in gate.splitlines()
    assert "prior" not in gate.split("### Held-out")[1]


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
    assert "| abt_buy | 0.8000 | 1 | 0 | 0 |" in lines
    # gate view is counts only: no rule names, no per-rule F1.
    assert "evidence_12" not in md.split("### Held-out")[1]


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
    assert "| walmart_amazon | MISSING | MISSING | MISSING | MISSING |" in md


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
    assert "evidence_12" not in text.split("### Held-out")[1]
