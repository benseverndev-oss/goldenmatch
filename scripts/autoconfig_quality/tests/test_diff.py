from scripts.autoconfig_quality.diff import diff_scorecards, render_table

BASE = {"datasets": {
    "anchor_x": {"kind": "anchor",
                 "signals": {"blocking_cost": {"candidate_pairs": 1529},
                             "classification": {"zip5": "zip"}}},
    "febrl3": {"kind": "real", "f1": {"f1": 0.99}},
}}


def test_anchor_signal_change_fails():
    cur = {"datasets": {**BASE["datasets"],
        "anchor_x": {"kind": "anchor",
                     "signals": {"blocking_cost": {"candidate_pairs": 8_931_083},
                                 "classification": {"zip5": "zip"}}}}}
    rows, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"
    assert any(r["status"] == "FAIL" and r["dataset"] == "anchor_x" for r in rows)


def test_real_f1_drop_beyond_tol_fails():
    cur = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "f1": {"f1": 0.95}}}}
    _, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"


def test_real_f1_within_tol_passes():
    cur = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "f1": {"f1": 0.985}}}}
    _, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "PASS"


def test_skipped_is_neutral_and_anchor_error_fails():
    # anchor error -> FAIL
    cur = {"datasets": {
        "anchor_x": {"kind": "anchor", "signals": {"error": "boom"}},
        "febrl3": {"kind": "real", "f1": {"f1": 0.99}}}}
    _, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "FAIL"
    # real error alone -> neutral (PASS)
    cur2 = {"datasets": {**BASE["datasets"], "febrl3": {"kind": "real", "error": "flake"}}}
    _, verdict2 = diff_scorecards(cur2, BASE, tolerance=0.01)
    assert verdict2 == "PASS"


def test_absent_dataset_is_neutral():
    cur = {"datasets": {"anchor_x": BASE["datasets"]["anchor_x"]}}  # febrl3 absent
    rows, verdict = diff_scorecards(cur, BASE, tolerance=0.01)
    assert verdict == "PASS"
    assert any(r["dataset"] == "febrl3" and r["status"] == "NEUTRAL" for r in rows)


def test_render_table_smoke():
    rows, _ = diff_scorecards(BASE, BASE)
    assert isinstance(render_table(rows), str)
