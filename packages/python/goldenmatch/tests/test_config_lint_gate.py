"""Pre-flight gate: warn-and-run by default, refuse only under strict.

Locks the wiring of the linter into dedupe_df/match_df: findings are attached
to the result and surfaced, the default mode never blocks a run, `off` skips,
and `strict` refuses on an error-severity finding.
"""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest
from goldenmatch import dedupe_df
from goldenmatch._api import _run_config_lint
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.config_lint import ConfigLintError, Finding, Severity


def _degenerate_cfg() -> GoldenMatchConfig:
    # block on a near-unique id -> blocking.near_unique fires
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            threshold=0.85,
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["id"])]),
    )


def _df(n=30):
    return pl.DataFrame({"id": [str(i) for i in range(n)], "name": ["Smith"] * n})


def test_dedupe_df_warn_attaches_findings_and_does_not_raise(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_CONFIG_LINT", raising=False)  # default == warn
    res = dedupe_df(_df(), config=_degenerate_cfg())
    rule_ids = {f.rule_id for f in res.lint_findings}
    assert "blocking.near_unique" in rule_ids  # surfaced, run still completed


def test_config_lint_off_skips(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_CONFIG_LINT", "off")
    res = dedupe_df(_df(), config=_degenerate_cfg())
    assert res.lint_findings == []


def test_strict_refuses_on_error_finding(monkeypatch):
    err = Finding(rule_id="x.y", severity=Severity.ERROR, message="boom",
                  rationale="r", doc_anchor="config-linter#x")
    monkeypatch.setattr("goldenmatch.core.config_lint.lint", lambda c, i: [err])

    monkeypatch.setenv("GOLDENMATCH_CONFIG_LINT", "strict")
    with pytest.raises(ConfigLintError):
        _run_config_lint(_df(), SimpleNamespace())

    # the SAME error finding does not raise in warn mode -- it warns and runs
    monkeypatch.setenv("GOLDENMATCH_CONFIG_LINT", "warn")
    assert _run_config_lint(_df(), SimpleNamespace()) == [err]


def test_lint_is_fail_open_on_internal_error(monkeypatch):
    # a linter that blows up must not break the run (returns [], never raises)
    def boom(_c, _i):
        raise RuntimeError("nope")
    monkeypatch.setattr("goldenmatch.core.config_lint.lint", boom)
    monkeypatch.setenv("GOLDENMATCH_CONFIG_LINT", "warn")
    assert _run_config_lint(_df(), SimpleNamespace()) == []
