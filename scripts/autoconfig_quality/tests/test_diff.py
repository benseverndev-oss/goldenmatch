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


def test_anchor_with_f1_floor_is_gated():
    base = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                           "f1": {"f1": 0.95}}}}
    # F1 drop beyond tol on an anchor that carries an F1 floor -> FAIL
    cur = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                          "f1": {"f1": 0.90}}}}
    _, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "FAIL"
    # within tol -> PASS
    cur2 = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                           "f1": {"f1": 0.945}}}}
    _, verdict2 = diff_scorecards(cur2, base, tolerance=0.01)
    assert verdict2 == "PASS"


def test_planner_rung_drift_is_warn_not_fail():
    # planner_rung is host-coupled (native availability / box size) -> WARN, never
    # FAIL, so a CI runner without the native wheel doesn't flap a native-on baseline.
    base = {"datasets": {"anchor_x": {"kind": "anchor",
        "signals": {"classification": {"zip5": "zip"},
                    "planner_rung": {"backend": "polars-direct", "rule_name": "small"}}}}}
    cur = {"datasets": {"anchor_x": {"kind": "anchor",
        "signals": {"classification": {"zip5": "zip"},
                    "planner_rung": {"backend": "bucket", "rule_name": "small_fast_box"}}}}}
    rows, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "PASS"
    planner_rows = [r for r in rows if r["field"].startswith("planner_rung")]
    assert planner_rows and all(r["status"] == "WARN" for r in planner_rows)
    # a real kernel-signal change alongside planner drift still FAILs
    cur2 = {"datasets": {"anchor_x": {"kind": "anchor",
        "signals": {"classification": {"zip5": "identifier"},
                    "planner_rung": {"backend": "bucket", "rule_name": "small"}}}}}
    _, verdict2 = diff_scorecards(cur2, base, tolerance=0.01)
    assert verdict2 == "FAIL"


def test_anchor_f1_crash_fails_not_silently_dropped():
    # The floor must never be silently dropped. If the F1 tier was attempted and
    # crashed (top-level "error"), the floored anchor FAILs -- not pass-by-omission.
    base = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                           "f1": {"f1": 0.99}}}}
    cur = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                          "error": "boom in evaluate_f1"}}}
    rows, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "FAIL"
    assert any(r["field"] == "f1" and r["status"] == "FAIL" for r in rows)


def test_anchor_f1_fastonly_skip_is_warn_not_fail():
    # An intentional fast-only run produces no f1 and no error: the floor isn't
    # measured, but that's surfaced as WARN (visible), never a silent pass and
    # never a FAIL (config-only runs are legitimate; CI runs the full tier).
    base = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1},
                                           "f1": {"f1": 0.99}}}}
    cur = {"datasets": {"anchor_person": {"kind": "anchor", "signals": {"x": 1}}}}
    rows, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "PASS"
    assert any(r["field"] == "f1" and r["status"] == "WARN" for r in rows)


def test_real_f1_probabilistic_floored():
    base = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.46}, "f1_probabilistic": {"f1": 0.82}}}}
    # probabilistic drop beyond tol -> FAIL (even though default f1 is unchanged)
    cur = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.46}, "f1_probabilistic": {"f1": 0.70}}}}
    _, verdict = diff_scorecards(cur, base, tolerance=0.01)
    assert verdict == "FAIL"
    # both within tol -> PASS
    cur2 = {"datasets": {"hist": {"kind": "real",
        "f1": {"f1": 0.455}, "f1_probabilistic": {"f1": 0.815}}}}
    _, verdict2 = diff_scorecards(cur2, base, tolerance=0.01)
    assert verdict2 == "PASS"


def test_render_table_smoke():
    rows, _ = diff_scorecards(BASE, BASE)
    assert isinstance(render_table(rows), str)
