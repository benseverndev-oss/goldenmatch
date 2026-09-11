"""Per-rule link-cut sweep (FS link-cut routing P3)."""

from __future__ import annotations

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
    )
    assert rec["applied"] is True and rec["voided"] is False
    assert (rec["f1"], rec["pairs"], rec["digest"]) == (0.9, 4, "abc")
    assert rec["matchkeys"]["fs"]["cut_rule"] == "evidence_9"


def test_arm_record_uncomputable_rule_is_not_applied_but_not_voided():
    reason = "otsu unavailable: needs a training histogram of more than 50 pairs; default rule"
    rec = S.arm_record("otsu", _SUMMARY, _stats(fs=_entry("prior_mid", reason)), (0, "x"))
    assert rec["applied"] is False and rec["voided"] is False


def test_arm_record_overridden_pin_is_voided():
    """KNOWN-POSITIVE for the pinned-arm assertion: an env var that switched on
    posterior scoring voids the arm instead of silently measuring the default."""
    reason = "link_cut_rule=evidence_9 not applied: posterior scoring"
    rec = S.arm_record("evidence_9", _SUMMARY, _stats(fs=_entry(None, reason)), (0, "x"))
    assert rec["applied"] is False and rec["voided"] is True


def test_arm_record_without_a_report_is_voided():
    assert S.arm_record("default", _SUMMARY, {}, (0, "x"))["voided"] is True
    assert (
        S.arm_record("default", _SUMMARY, _stats(fs=_entry("prior_mid", "default rule")), (0, "x"))[
            "voided"
        ]
        is False
    )


def test_cut_env_overrides_reports_only_non_empty_values():
    env = {"GOLDENMATCH_FS_EVIDENCE_CUT": "6", "GOLDENMATCH_FS_CALIBRATED": " ", "PATH": "x"}
    assert S.cut_env_overrides(env) == {"GOLDENMATCH_FS_EVIDENCE_CUT": "6"}


def test_every_corpus_name_resolves_to_a_loader():
    import pytest

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


def test_sweep_frame_runs_every_arm_on_one_saved_model(tmp_path, monkeypatch):
    for var in S.CUT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from scripts.autoconfig_quality.anchors import gen_labeled

    df, gt = gen_labeled(n_entities=60, seed=7)
    out = S.sweep_frame(df, gt, tmp_path)

    assert set(out["arms"]) == set(S.ARMS)
    assert not [a for a, rec in out["arms"].items() if rec["voided"]]
    not_applied = [r for r in CUT_RULES if r != "otsu" and not out["arms"][r]["applied"]]
    assert not not_applied, not_applied
    assert out["models_saved"], "the default arm must save the EM model the other arms load"
    assert all(d and "midpoint_bits" in d for d in out["cut_diagnostics"].values())
    assert set(out["below_default"]) <= set(CUT_RULES)
    assert isinstance(out["model_reload_delta"], float)
