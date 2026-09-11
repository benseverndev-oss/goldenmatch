"""Link-cut routing gate (FS link-cut routing P4)."""

from __future__ import annotations

import json

from goldenmatch.core import fs_cut_rules
from goldenmatch.core.fs_cut_rules import CutRow

from scripts.autoconfig_quality import cut_gate as G
from scripts.autoconfig_quality import cut_rows
from scripts.autoconfig_quality import rule_sweep as S

_SPARSE = CutRow(
    name="sparse", rule="evidence_12", reason="r", when=lambda d: d.proportion_matched < 0.01
)


def _diag(lam: float = 0.002, mid: float = 3.0, prior: float = 9.0, n_fields: int = 3) -> dict:
    return {
        "proportion_matched": lam,
        "lo": -10.0,
        "hi": 30.0,
        "midpoint_bits": mid,
        "prior_bits": prior,
        "n_fields": n_fields,
        "has_negative_evidence": False,
        "field_weight_spans": {},
        "admitted_fraction": None,
    }


def _rec(
    f1: dict | None = None,
    *,
    diags: dict | None = None,
    metric: str = "f1",
    labelled: dict | None = None,
    crashed: dict | None = None,
    routed_rules: dict | None = None,
    routed_matches: bool | None = True,
) -> dict:
    f1 = f1 or {}
    diags = {"fs": _diag()} if diags is None else diags
    arms = {}
    for arm in S.ARMS:
        lab = None
        if labelled is not None:
            value = labelled.get(arm, labelled["default_loaded"])
            lab = {"f1": value, "precision": value, "recall": value, "labelled_pairs": 10}
        arms[arm] = {
            "f1": f1.get(arm, f1.get("default_loaded", 0.9)),
            "applied": True,
            "voided": False,
            "crashed": (crashed or {}).get(arm),
            "digest": "d",
            "matchkeys": {},
            "labelled": lab,
        }
    return {
        "probabilistic_matchkeys": sorted(diags),
        "cut_diagnostics": diags,
        "gate_metric": metric,
        "arms": arms,
        "routed_rules": {mk: None for mk in diags} if routed_rules is None else routed_rules,
        "routed_matches": routed_matches,
    }


def _card(sha: str = "abc", failures: list[str] | None = None, **datasets) -> dict:
    return {"meta": {"git_sha": sha, "failures": failures or []}, "datasets": datasets}


# ─── one dataset ──────────────────────────────────────────────────────────────


def test_a_dataset_no_row_fires_on_keeps_the_default_and_passes():
    v = G.judge(_rec(diags={"fs": _diag(lam=0.2)}), (_SPARSE,))
    assert (v.passed, v.fired, v.rule, v.problem) == (True, False, None, None)


def test_a_routed_rule_within_the_tolerance_passes():
    v = G.judge(_rec({"default_loaded": 0.9, "evidence_12": 0.891}), (_SPARSE,))
    assert (v.passed, v.fired, v.rule, v.delta) == (True, True, "evidence_12", -0.009)


def test_a_routed_rule_below_the_tolerance_fails():
    v = G.judge(_rec({"default_loaded": 0.9, "evidence_12": 0.88}), (_SPARSE,))
    assert (v.passed, v.delta) == (False, -0.02)


def test_a_crashed_routed_arm_fails():
    v = G.judge(_rec(crashed={"evidence_12": "memory_cap"}), (_SPARSE,))
    assert (v.passed, v.problem) == (False, "evidence_12 arm memory_cap")


def test_magellan_records_gate_on_the_labelled_metric():
    worse_labelled = _rec(
        {"default_loaded": 0.5, "evidence_12": 0.9},
        metric="labelled",
        labelled={"default_loaded": 0.8, "evidence_12": 0.7},
    )
    assert G.judge(worse_labelled, (_SPARSE,)).passed is False
    worse_plain = _rec(
        {"default_loaded": 0.9, "evidence_12": 0.5},
        metric="labelled",
        labelled={"default_loaded": 0.7, "evidence_12": 0.8},
    )
    assert G.judge(worse_plain, (_SPARSE,)).passed is True


def test_matchkeys_routed_to_different_rules_fail_as_unmeasurable():
    rec = _rec(diags={"fs_a": _diag(lam=0.002), "fs_b": _diag(lam=0.2)})
    v = G.judge(rec, (_SPARSE,))
    assert v.passed is False and "different rules" in v.problem


def test_a_record_without_a_gate_metric_fails():
    rec = _rec()
    del rec["gate_metric"]
    v = G.judge(rec, (_SPARSE,))
    assert v.passed is False and "gate_metric" in v.problem


# ─── one set ──────────────────────────────────────────────────────────────────


def test_a_set_fails_when_an_expected_dataset_is_missing_or_the_run_failed():
    card = _card(febrl3=_rec())
    missing = G.gate_set(card, ["febrl3", "febrl4"], (_SPARSE,), check_routed=False)
    assert missing.passed is False and missing.missing == ["febrl4"]
    failed = G.gate_set(
        _card(failures=["x: voided"], febrl3=_rec()), ["febrl3"], (_SPARSE,), check_routed=False
    )
    assert failed.passed is False and failed.problems == ["the matrix run recorded 1 failures"]
    ok = G.gate_set(card, ["febrl3"], (_SPARSE,), check_routed=False)
    assert (ok.passed, ok.fired, ok.measured) == (True, 1, 1)


def test_the_shipped_table_also_needs_the_routed_arm_to_agree():
    agree = _rec(routed_rules={"fs": "evidence_12"})
    assert G.gate_set(_card(d=agree), ["d"], (_SPARSE,), check_routed=True).passed is True

    picked_other = _rec(routed_rules={"fs": None})
    v = G.gate_set(_card(d=picked_other), ["d"], (_SPARSE,), check_routed=True).verdicts["d"]
    assert v.passed is False and "picked differently" in v.problem

    split = _rec(routed_rules={"fs": "evidence_12"}, routed_matches=False)
    v = G.gate_set(_card(d=split), ["d"], (_SPARSE,), check_routed=True).verdicts["d"]
    assert v.passed is False and "partition differs" in v.problem

    old = _rec()
    del old["routed_rules"]
    assert G.gate_set(_card(d=old), ["d"], (_SPARSE,), check_routed=True).passed is False


def test_a_crashed_routed_arm_is_named_instead_of_a_disagreement():
    """A crashed or voided routed arm records ``routed_rules = {}``, which used to read
    as a disagreement with the recorded diagnostics -- naming the wrong cause."""
    crashed_routed = _rec(routed_rules={}, crashed={"routed": "timeout"})
    v = G.gate_set(_card(d=crashed_routed), ["d"], (_SPARSE,), check_routed=True).verdicts["d"]
    assert v.passed is False and v.problem == "the routed arm timeout"


# ─── rendering and CLI ────────────────────────────────────────────────────────


def test_the_held_out_verdict_names_no_dataset_rule_or_score():
    design = G.gate_set(
        _card(febrl3=_rec({"default_loaded": 0.9, "evidence_12": 0.95})),
        ["febrl3"],
        (_SPARSE,),
        check_routed=False,
    )
    holdout = G.gate_set(
        _card(walmart_amazon=_rec({"default_loaded": 0.9, "evidence_12": 0.8})),
        ["walmart_amazon"],
        (_SPARSE,),
        check_routed=False,
    )
    md = G.render("candidate sparse -> evidence_12", design, holdout)
    design_part, held_out_part = md.split("**Held-out")
    assert "| febrl3 | evidence_12 | +0.0500 | PASS |" in design_part
    assert "**Held-out" + held_out_part.split("\n")[0] == "**Held-out: FAIL** (fired on 1 of 1)"
    for leaked in ("walmart_amazon", "evidence_12", "0.8", "-0.1000"):
        assert leaked not in held_out_part


def _write_cards(tmp_path, design: dict, holdout: dict):
    d, h = tmp_path / "design.json", tmp_path / "holdout.json"
    d.write_text(json.dumps(design), encoding="utf-8")
    h.write_text(json.dumps(holdout), encoding="utf-8")
    return d, h


def test_main_exits_1_when_a_candidate_fails_held_out_and_0_with_report_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cut_rows, "CANDIDATES", (_SPARSE,))
    monkeypatch.setattr(fs_cut_rules, "ROWS", ())
    monkeypatch.setattr(
        G, "ci_datasets", lambda corpus: ["febrl3"] if corpus == "design" else ["walmart_amazon"]
    )
    d, h = _write_cards(
        tmp_path,
        _card(febrl3=_rec()),
        _card(walmart_amazon=_rec({"default_loaded": 0.9, "evidence_12": 0.5})),
    )
    out = tmp_path / "gate.md"
    argv = ["--design", str(d), "--holdout", str(h), "--table", "candidates", "--out", str(out)]
    assert G.main(argv) == 1
    assert "**Held-out: FAIL**" in out.read_text(encoding="utf-8")
    assert G.main([*argv, "--report-only"]) == 0


def test_main_refuses_matrices_from_different_commits(tmp_path):
    d, h = _write_cards(tmp_path, _card("abc"), _card("def"))
    argv = [
        "--design",
        str(d),
        "--holdout",
        str(h),
        "--table",
        "shipped",
        "--out",
        str(tmp_path / "g.md"),
    ]
    assert G.main(argv) == 2


def test_the_empty_shipped_table_passes_when_every_routed_arm_kept_the_default(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(fs_cut_rules, "ROWS", ())
    monkeypatch.setattr(
        G, "ci_datasets", lambda corpus: ["febrl3"] if corpus == "design" else ["abt_buy"]
    )
    d, h = _write_cards(tmp_path, _card(febrl3=_rec()), _card(abt_buy=_rec()))
    out = tmp_path / "gate.md"
    argv = ["--design", str(d), "--holdout", str(h), "--table", "shipped", "--out", str(out)]
    assert G.main(argv) == 0
    assert "shipped table (no rows)" in out.read_text(encoding="utf-8")


# ─── the candidates, as written from the design matrix ────────────────────────


def test_no_candidate_is_already_shipped():
    shipped = {row.name for row in fs_cut_rules.ROWS}
    assert not shipped & {row.name for row in cut_rows.CANDIDATES}
