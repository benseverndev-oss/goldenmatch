"""gym-gate #1934: a blessed built-rule pair that RAN this run but is unmeasurable
(``no_damage``/``n/a`` -- the config drifted degenerate enough that the damage no
longer bites, even though the dataset's ceiling cleared the #1620 degenerate-skip
floor) is SKIPPED (advisory), not MISSING (fail). ``error`` / truly-absent pairs
stay MISSING.

Pins the residual bench-suggest-quality failure #1975 did NOT cover: ncvr_synthetic's
ceiling sat just ABOVE the 0.50 floor, so run_catalog did not emit a degenerate-skip
sentinel; it ran the full pipeline, its 5 damage scenarios came back no_damage, and
the gate re-failed them as MISSING -- despite zero recovery regression.
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


def _status_record(dataset, status):
    # A record that RAN but is non-ok this run (no_damage / n/a / error).
    return {"dataset": dataset, "name": "threshold_too_low", "status": status,
            "builds_on_existing_rule": True}


def test_no_damage_pair_is_advisory_not_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'degen' ran but its damage no longer degrades -> status no_damage (unmeasurable).
    records = [_ok_record("good", 1.0, 1.0), _status_record("degen", "no_damage")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "MISSING" not in out
    assert rc == 0, out
    assert "0 missing" in out and "1 skipped" in out


def test_na_pair_is_advisory_not_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    records = [_ok_record("good", 1.0, 1.0), _status_record("degen", "n/a")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "MISSING" not in out
    assert rc == 0, out


def test_errored_pair_still_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # A genuine error is NOT unmeasurable-benign -> stays MISSING/FAIL.
    records = [_ok_record("good", 1.0, 1.0), _status_record("degen", "error")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert rc == 1, out


def test_absent_pair_still_missing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'degen' simply absent (no record at all) -> MISSING/FAIL.
    records = [_ok_record("good", 1.0, 1.0)]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert rc == 1, out


def test_real_regression_not_masked_by_unmeasurable_skip(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_loads_gym_baseline", _baseline)
    # 'good' recovery craters (real regression) while 'degen' is no_damage-skipped;
    # the real drop must still gate -- the unmeasurable skip doesn't mask it.
    records = [_ok_record("good", 0.2, 0.2), _status_record("degen", "no_damage")]
    rc = cli._cmd_gym_gate(records, "test", "deadbeef")
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL" in out
