import json

from scripts.autoconfig_quality.scorecard import build_scorecard, dumps, loads


def test_build_scorecard_shape_and_stability():
    results = {
        "anchor_x": {"kind": "anchor", "signals": {"blocking_cost": {"candidate_pairs": 1529}}},
        "febrl3": {"kind": "real", "signals": {}, "f1": {"f1": 0.991}},
    }
    sc = build_scorecard(results, native_version="0.1.11", git_sha="abc123",
                         skipped={"ncvr": "absent"})
    assert sc["meta"]["native_version"] == "0.1.11"
    assert sc["meta"]["git_sha"] == "abc123"
    assert sc["meta"]["datasets_skipped"] == {"ncvr": "absent"}
    assert sorted(sc["meta"]["datasets_run"]) == ["anchor_x", "febrl3"]
    assert "recorded_at" not in json.dumps(sc)        # NO timestamp -> byte-stable
    assert loads(dumps(sc)) == sc                      # round-trips


def test_floats_are_rounded_for_byte_stability():
    results = {"d": {"kind": "real", "f1": {"f1": 0.123456789123}}}
    sc = build_scorecard(results, native_version="x", git_sha="y")
    assert sc["datasets"]["d"]["f1"]["f1"] == round(0.123456789123, 6)


def test_two_runs_same_input_are_byte_identical():
    results = {"d": {"kind": "anchor", "signals": {"r": 0.5}}}
    a = dumps(build_scorecard(results, native_version="v", git_sha="s"))
    b = dumps(build_scorecard(results, native_version="v", git_sha="s"))
    assert a == b
