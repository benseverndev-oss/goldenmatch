"""gym-gate: a blessed dataset that drifts into a degenerate zero-config ceiling
is SKIPPED (advisory), not MISSING (fail), and the headline compares over the
COMMON measurable population so the drop-out can't phantom-shift the mean.

Pins the #1934/bench-suggest-quality fix: ncvr_synthetic's ceiling fell below the
0.50 floor between blesses, so run_catalog skipped it -> the gate re-failed every
ncvr_synthetic pair as MISSING and the shrunken headline_raw mean tripped the
gate, despite zero per-pair recovery regression.
"""
from scripts.suggest_quality import cli


def _bpair(rec_live, rec_raw, *, ok=True, built=True):
    return {
        "status": "ok" if ok else "error",
        "builds_on_existing_rule": built,
        "recovery_pct_live": rec_live,
        "recovery_pct_raw": rec_raw,
    }


def _baseline():
    # Two datasets, one built-rule ok pair each; headlines are the 2-pair means.
    return {
        "headline_live": 0.75,
        "headline_raw": 0.85,
        "pairs": {
            "good/threshold_too_low": _bpair(1.0, 1.0),
            "degen/threshold_too_low": _bpair(0.5, 0.7),
        },
    }


def _ok_record(dataset, live, raw):
    return {"dataset": dataset, "name": "threshold_too_low", "status": "ok",
            "builds_on_existing_rule": True,
            "recovery_pct_live": live, "recovery_pct_raw": raw,
            "expected_rule": "r"}


def _skip_sentinel(dataset):
    return {"dataset": dataset, "name": "*",
            "status": "skipped_degenerate_ceiling", "f1_ceiling": 0.26,
            "ceiling_floor": 0.50}


def test_degenerate_skip_is_advisory_not_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'good' still measurable (unchanged); 'degen' skipped for a degenerate ceiling.
    records = [_ok_record("good", 1.0, 1.0), _skip_sentinel("degen")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "MISSING" not in out
    # PASS: the degenerate drop-out is advisory and the common-set headline (over
    # 'good' only) is unchanged -> no phantom headline_raw failure.
    assert rc == 0, out
    assert "0 missing" in out and "1 skipped" in out


def test_absent_without_skip_sentinel_still_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'degen' simply absent (erroring / removed) -- NO skip sentinel -> MISSING/FAIL.
    records = [_ok_record("good", 1.0, 1.0)]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert rc == 1, out


def test_real_regression_on_measured_dataset_still_fails(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'good' recovery craters while 'degen' is skipped -> the real drop still gates.
    records = [_ok_record("good", 0.2, 0.2), _skip_sentinel("degen")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL" in out
