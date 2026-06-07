"""FD-driven negative evidence (GoldenCheck -> GoldenMatch door #3).

GoldenCheck's discovered functional dependencies admit high-cardinality identity
anchors as negative-evidence fields even when GoldenMatch's name-based
identity_score misses them. Additive + flag-gated (default OFF).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
from goldenmatch.core.complexity_profile import ColumnPrior
from goldenmatch.core.quality import _goldencheck_available, fd_identity_scores

pytestmark = pytest.mark.skipif(not _goldencheck_available(), reason="goldencheck not installed")


def _anchor_df(n: int = 120) -> pl.DataFrame:
    # 'acct': cardinality 0.6, strictly determines 'name' (an identity anchor);
    # 'email' is the exact-matchkey field; 'amt' is noise.
    accts = [1000 + k for k in range(48)] + [v for k in range(24) for v in (2000 + k,) * 3]
    a2name = {a: f"name_{a}" for a in set(accts)}
    return pl.DataFrame({
        "acct": [str(a) for a in accts],
        "name": [a2name[a] for a in accts],
        "email": [f"e{i}@x.com" for i in range(len(accts))],
        "amt": [i % 5 for i in range(len(accts))],
    })


def _config() -> GoldenMatchConfig:
    # An exact matchkey on 'email'. 'acct' is NOT a matchkey field and NOT in
    # blocking -> eligible for NE if the identity/FD gate admits it.
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="mk_email", type="exact", fields=[MatchkeyField(field="email")])],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
    )


def _priors() -> dict[str, ColumnPrior]:
    # 'acct' has a LOW name-based identity_score -> the name heuristic alone
    # would never promote it; only the FD anchor path can.
    return {
        "acct": ColumnPrior(identity_score=0.30, corruption_score=0.0),
        "name": ColumnPrior(identity_score=0.20, corruption_score=0.0),
        "email": ColumnPrior(identity_score=0.95, corruption_score=0.0),
        "amt": ColumnPrior(identity_score=0.0, corruption_score=0.0),
    }


def _ne_fields(config: GoldenMatchConfig) -> set[str]:
    return {ne.field for mk in config.matchkeys for ne in (mk.negative_evidence or [])}


# --- fd_identity_scores bridge ----------------------------------------------

def test_fd_identity_scores_finds_anchor() -> None:
    scores = fd_identity_scores(_anchor_df())
    assert scores is not None
    assert scores.get("acct", 0.0) >= 0.95


def test_fd_identity_scores_clean_is_none() -> None:
    df = pl.DataFrame({"a": [i % 5 for i in range(120)], "b": [(i * 3) % 7 for i in range(120)]})
    assert fd_identity_scores(df) is None


# --- promote_negative_evidence FD path --------------------------------------

def test_fd_anchor_admitted_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_FD_NEGATIVE_EVIDENCE", "1")
    out = promote_negative_evidence(_config(), _anchor_df(), _priors())
    assert "acct" in _ne_fields(out)  # admitted via FD despite low identity_score
    ne = next(n for mk in out.matchkeys for n in (mk.negative_evidence or []) if n.field == "acct")
    assert 0.0 < ne.penalty <= 0.3  # scaled by FD confidence, capped at default


def test_fd_anchor_not_admitted_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_FD_NEGATIVE_EVIDENCE", raising=False)
    out = promote_negative_evidence(_config(), _anchor_df(), _priors())
    assert "acct" not in _ne_fields(out)  # name heuristic alone misses it


def test_high_identity_path_unchanged_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # A genuine name-identity column (high identity_score) is still admitted the
    # normal way, with the default (unscaled) penalty.
    monkeypatch.setenv("GOLDENMATCH_FD_NEGATIVE_EVIDENCE", "1")
    df = _anchor_df()
    # make 'acct' also high identity_score -> name path; penalty stays default.
    priors = _priors()
    priors["acct"] = ColumnPrior(identity_score=0.95, corruption_score=0.0)
    out = promote_negative_evidence(_config(), df, priors)
    ne = next((n for mk in out.matchkeys for n in (mk.negative_evidence or []) if n.field == "acct"), None)
    assert ne is not None and ne.penalty == 0.3
