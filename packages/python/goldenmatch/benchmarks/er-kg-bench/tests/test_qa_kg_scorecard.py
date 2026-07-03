"""Slice D KG-vs-KG scorecard -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e import kg_scorecard as ks


def test_parse_entity_set_finds_all_known_surfaces():
    s2c = {"Apple": "a", "Cupertino": "c", "Widgets": "w"}
    answer = "Apple, Cupertino and also Widgets."
    assert ks.parse_entity_set(answer, s2c) == {"a", "c", "w"}


def test_parse_entity_set_ignores_unknown_and_dedups():
    s2c = {"Apple": "a"}
    assert ks.parse_entity_set("Apple Apple Bogus", s2c) == {"a"}


def test_parse_entity_set_empty_on_no_match():
    assert ks.parse_entity_set("nothing here", {"Apple": "a"}) == set()


def _good_result():
    # oracle >= goldengraph >= exact_match >= none per metric; goldengraph beats exact by a
    # margin on both; exact_match ~= none on bridge-recall (the slice-A name_only==none finding).
    bridge = {"oracle": 1.0, "goldengraph": 0.558, "exact_match": 0.234, "none": 0.234}
    aggf1 = {"oracle": 1.0, "goldengraph": 1.0, "exact_match": 0.45, "none": 0.10}
    return ks.ScorecardResult(bridge_recall=bridge, aggregation_f1=aggf1)


def test_gate_passes_on_well_formed_scorecard():
    res = _good_result()
    hard = [(lbl, ok) for lbl, ok, is_hard in ks.evaluate_assertions(res) if is_hard]
    assert all(ok for _lbl, ok in hard), hard
    assert ks.gate_exit_code(res) == 0


def test_gate_fails_when_no_moat():
    res = _good_result()
    res.aggregation_f1["exact_match"] = 0.99  # goldengraph 1.0 - 0.99 < MOAT_MARGIN
    assert ks.gate_exit_code(res) == 1


def test_gate_fails_when_monotonicity_violated():
    res = _good_result()
    res.aggregation_f1["none"] = 0.50  # none > exact_match 0.45 -> mono fails (moat+inert stay green)
    assert ks.gate_exit_code(res) == 1


def test_gate_fails_when_exact_beats_none_on_bridge():
    res = _good_result()
    res.bridge_recall["none"] = 0.05  # exact 0.234 >> none + EPS -> exact not inert
    assert ks.gate_exit_code(res) == 1


def test_render_md_is_ascii_and_has_both_capabilities():
    md = ks.render_scorecard_md(_good_result())
    assert md.isascii()
    assert "bridge_recall" in md and "aggregation" in md and "## verdicts" in md


def test_framework_set_f1_scores_parsed_answers():
    s2c = {"Apple": "a", "Widgets": "w", "Cupertino": "c"}
    got = ks.framework_set_f1(answers=["Apple and Widgets."], golds=[{"a", "w"}], s2c=s2c)
    assert got == 1.0  # perfect set match -> F1 1.0


def test_framework_set_f1_partial():
    s2c = {"Apple": "a", "Widgets": "w"}
    got = ks.framework_set_f1(answers=["Apple only."], golds=[{"a", "w"}], s2c=s2c)
    assert 0.0 < got < 1.0  # recall 0.5 -> F1 0.667
