"""FS field-dependence correction (GOLDENMATCH_FS_FIELD_DEPENDENCE).

FS sums per-field weights assuming conditional independence. When two fields
co-agree at their top level more than independence predicts among non-matches
(e.g. first_name x surname namesakes), FS over-counts ``log2(lift)`` bits and
over-merges. EM estimates the excess-lift per correlated pair; scoring subtracts
it when both agree. Default OFF = byte-identical.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import probabilistic as P
from goldenmatch.core.probabilistic import (
    EMResult,
    _compute_joint_corrections,
    _fs_field_dependence_enabled,
    _joint_correction_scalar,
)

FLAG = "GOLDENMATCH_FS_FIELD_DEPENDENCE"


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)


def _mk():
    return MatchkeyConfig(
        name="fs", type="probabilistic", threshold=0.8,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="surname", scorer="jaro_winkler", levels=2),
            MatchkeyField(field="city", scorer="exact", levels=2),
        ],
    )


def test_flag_default_off():
    assert _fs_field_dependence_enabled() is False


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled"])
def test_flag_truthy(monkeypatch, truthy):
    monkeypatch.setenv(FLAG, truthy)
    assert _fs_field_dependence_enabled() is True


def test_detects_correlated_pair():
    # first & surname co-agree at top level correlated among non-matches.
    rows = [[1, 1, 0]] * 40 + [[0, 0, 0]] * 40 + [[1, 0, 0]] * 10 + [[0, 1, 0]] * 10
    comp = np.array(rows, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    jc = _compute_joint_corrections(comp, _mk(), m, u, 0.05, cond, set())
    pair = [t for t in jc if {t[0], t[1]} == {"first_name", "surname"}]
    assert pair, "should detect first_name x surname correlation"
    assert pair[0][2] >= P._FD_MIN_BITS


def test_no_correction_when_independent():
    # Fields agree independently -> no excess -> no correction.
    rng = np.random.default_rng(0)
    a = rng.integers(0, 2, 200)
    b = rng.integers(0, 2, 200)  # independent of a
    comp = np.stack([a, b, np.zeros(200, dtype=np.int64)], axis=1)
    cond = np.zeros((200, 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    jc = _compute_joint_corrections(comp, _mk(), m, u, 0.05, cond, set())
    assert not any({t[0], t[1]} == {"first_name", "surname"} for t in jc)


def _em(joint=None):
    return EMResult(
        m_probs={}, u_probs={},
        match_weights={"first_name": [-1.0, 2.0], "surname": [-1.0, 2.0],
                       "city": [-1.0, 2.0]},
        converged=True, iterations=1, proportion_matched=0.05,
        joint_corrections=joint,
    )


def test_scoring_subtracts_when_both_top():
    em = _em([("first_name", "surname", 1.5)])
    assert _joint_correction_scalar([1, 1, 0], _mk(), em) == -1.5   # both top
    assert _joint_correction_scalar([1, 0, 0], _mk(), em) == 0.0    # partial
    assert _joint_correction_scalar([0, 0, 0], _mk(), em) == 0.0    # neither


def test_scoring_noop_when_off():
    assert _joint_correction_scalar([1, 1, 0], _mk(), _em(None)) == 0.0


def test_emresult_roundtrip_carries_corrections():
    em = _em([("first_name", "surname", 1.5)])
    back = EMResult.from_dict(em.to_dict())
    assert back.joint_corrections == [("first_name", "surname", 1.5)]
    # off -> not serialized
    assert "joint_corrections" not in _em(None).to_dict()


def test_detects_a_pair_the_blocking_key_conditions():
    """A BLOCKING-conditioned pair must be corrected too — the case that
    matters, and the one this used to exclude.

    `_neutral_u_for` gives a conditioned field a fixed u ([0.50, 0.50]) rather
    than a random-pair estimate, which bounds its MARGINAL weight to about
    log2(0.95/0.50) = +0.93 bits. That says nothing about the joint: two
    conditioned fields each contribute ~0.93 bits SUMMED as independent, while
    among blocked non-matches they co-agree far above the 0.25 independence
    baseline their neutral priors imply.

    On a name-blocked person shape both first_name and surname are conditioned,
    so excluding them meant the dominant correlated pair — 5.57x lift, +2.48
    bits, 97% of false merges (ADR 0065) — could never be evaluated, and the
    panel measured +0.0000 three times over.

    Same data as test_detects_correlated_pair; the ONLY difference is that both
    fields are declared blocking-conditioned.
    """
    rows = [[1, 1, 0]] * 40 + [[0, 0, 0]] * 40 + [[1, 0, 0]] * 10 + [[0, 1, 0]] * 10
    comp = np.array(rows, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    jc = _compute_joint_corrections(
        comp, _mk(), m, u, 0.05, cond, {"first_name", "surname"},
    )
    pair = [t for t in jc if {t[0], t[1]} == {"first_name", "surname"}]
    assert pair, (
        "a blocking-conditioned pair must still be corrected: the exclusion is "
        "about MARGINAL information, not the joint double-count"
    )
    assert pair[0][2] >= P._FD_MIN_BITS


def test_single_comparison_field_is_reported_not_silent():
    """Fewer than two comparison fields cannot produce a pair. That must SAY so
    rather than return an empty list, because 'enabled and found nothing' and
    'never enabled' were indistinguishable for three panel runs."""
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[MatchkeyField(field="only", scorer="exact", levels=2)],
    )
    comp = np.array([[1]] * 20 + [[0]] * 20, dtype=np.int64)
    cond = np.zeros((len(comp), 1), dtype=bool)
    m = {"only": [0.1, 0.9]}
    u = {"only": [0.5, 0.5]}
    assert _compute_joint_corrections(comp, mk, m, u, 0.05, cond, set()) == []


# ---------------------------------------------------------------------------
# Reach probe (GOLDENMATCH_FS_FD_PROBE)
#
# The probe exists to answer whether the correction can touch a pair whose
# decision is still open. Its useful answer is "none" -- which is exactly the
# answer a BROKEN probe also gives, so these tests pin the known-positive first
# and the empty case second. An empty verification is not a pass.
# ---------------------------------------------------------------------------

def _probe_arrays(weight_bits: float, n: int = 10):
    """log_m/log_u whose implied FS evidence is exactly ``weight_bits`` per pair."""
    log_u = np.zeros(n, dtype=np.float64)
    log_m = np.full(n, weight_bits * math.log(2), dtype=np.float64)
    top = {"a": np.ones(n, dtype=bool), "b": np.ones(n, dtype=bool)}
    return top, log_m, log_u


def test_probe_reports_flippable_pairs_when_they_exist(monkeypatch, caplog):
    """KNOWN-POSITIVE: pairs sitting inside [cut, cut+bits) are reported."""
    monkeypatch.setenv("GOLDENMATCH_FS_EVIDENCE_CUT", "10")
    top, log_m, log_u = _probe_arrays(11.0, n=10)
    with caplog.at_level("WARNING"):
        P._log_fd_reach_probe([("a", "b", 2.0)], top, log_m, log_u)
    msg = caplog.text
    assert "FLIPPABLE band [10.00, 12.00): 10 touched of 10" in msg, msg


def test_probe_reports_none_when_pairs_are_far_above_the_cut(monkeypatch, caplog):
    """The real-panel shape: touched pairs are overwhelming matches, band empty."""
    monkeypatch.setenv("GOLDENMATCH_FS_EVIDENCE_CUT", "9")
    top, log_m, log_u = _probe_arrays(40.0, n=10)
    with caplog.at_level("WARNING"):
        P._log_fd_reach_probe([("a", "b", 5.0)], top, log_m, log_u)
    msg = caplog.text
    assert "touches 10/10 pairs" in msg, msg
    assert "0 touched of 0" in msg, msg
    assert "+31.0 b above the cut" in msg, msg


def test_probe_skips_without_an_evidence_cut(monkeypatch, caplog):
    """No prior-invariant bar means no distance to measure -- say so, don't guess."""
    monkeypatch.delenv("GOLDENMATCH_FS_EVIDENCE_CUT", raising=False)
    top, log_m, log_u = _probe_arrays(11.0)
    with caplog.at_level("WARNING"):
        P._log_fd_reach_probe([("a", "b", 2.0)], top, log_m, log_u)
    assert "no GOLDENMATCH_FS_EVIDENCE_CUT is set" in caplog.text


@pytest.mark.parametrize("val,fires", [("1", True), ("0", False), ("", False)])
def test_probe_gated_by_its_own_env(monkeypatch, caplog, val, fires):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_FD_PROBE", val)
    monkeypatch.setenv("GOLDENMATCH_FS_EVIDENCE_CUT", "1")
    rows = [[1, 1, 0]] * 40 + [[0, 0, 0]] * 40 + [[1, 0, 0]] * 10 + [[0, 1, 0]] * 10
    comp = np.array(rows, dtype=np.int64)
    cond = np.zeros((len(comp), 3), dtype=bool)
    m = {"first_name": [0.1, 0.9], "surname": [0.1, 0.9], "city": [0.5, 0.5]}
    u = {"first_name": [0.5, 0.5], "surname": [0.5, 0.5], "city": [0.5, 0.5]}
    with caplog.at_level("WARNING"):
        _compute_joint_corrections(comp, _mk(), m, u, 0.05, cond, set())
    assert ("field-dependence probe" in caplog.text) is fires


# ---------------------------------------------------------------------------
# _combine_em_sessions must not silently drop EMResult fields
#
# The correction was estimated correctly, logged, applied by every scoring
# entry point -- and thrown away in transit by a hand-enumerated constructor
# that predated it. Every panel measurement of this lever, back to the original
# spike, scored a model the combine step had already stripped. The regression
# tests below pin the behaviour; the LAST one pins the CLASS, so the next field
# added to EMResult cannot repeat it silently.
# ---------------------------------------------------------------------------

def _sess_em(jc=None, thr=None):
    return EMResult(
        m_probs={f.field: [0.1, 0.9] for f in _mk().fields},
        u_probs={f.field: [0.5, 0.5] for f in _mk().fields},
        match_weights={f.field: [-2.3, 0.85] for f in _mk().fields},
        converged=True, iterations=5, proportion_matched=0.05,
        joint_corrections=jc, calibrated_link_threshold=thr,
    )


def test_combine_carries_joint_corrections():
    jc = [("first_name", "surname", 2.48)]
    out = P._combine_em_sessions(_mk(), [
        (("first_name",), _sess_em(jc), 100.0),
        (("surname",), _sess_em(jc), 100.0),
    ])
    assert out.joint_corrections == [("first_name", "surname", 2.48)]


def test_combine_pair_weights_a_pair_only_one_pass_found():
    """A pass that did not report the pair counts as 0 -- biased DOWNWARD on
    purpose, because these bits are subtracted and over-stating costs recall."""
    out = P._combine_em_sessions(_mk(), [
        (("first_name",), _sess_em([("first_name", "surname", 2.48)]), 100.0),
        (("surname",), _sess_em(None), 100.0),
    ])
    assert out.joint_corrections == [("first_name", "surname", pytest.approx(1.24))]


def test_combine_normalises_pair_order():
    out = P._combine_em_sessions(_mk(), [
        (("first_name",), _sess_em([("first_name", "surname", 2.48)]), 100.0),
        (("surname",), _sess_em([("surname", "first_name", 2.48)]), 100.0),
    ])
    assert out.joint_corrections == [("first_name", "surname", pytest.approx(2.48))]


def test_combine_drops_a_pair_below_the_floor_and_says_so(caplog):
    with caplog.at_level("WARNING"):
        out = P._combine_em_sessions(_mk(), [
            (("first_name",), _sess_em([("first_name", "surname", 0.6)]), 1.0),
            (("surname",), _sess_em(None), 100.0),
        ])
    assert out.joint_corrections is None
    assert "none survived the pair-weighted mean" in caplog.text


def test_combine_carries_calibrated_link_threshold():
    """Excluded, not zero-weighted: a pass that could not calibrate did not
    vote for 0.0, and averaging one in would drag the cut toward over-merge."""
    out = P._combine_em_sessions(_mk(), [
        (("first_name",), _sess_em(thr=0.90), 300.0),
        (("surname",), _sess_em(thr=None), 100.0),
    ])
    assert out.calibrated_link_threshold == pytest.approx(0.90)


def test_no_emresult_field_is_silently_dropped_by_combine():
    """THE CLASS GUARD.

    Every field on EMResult must be either carried through the combine or
    listed here as deliberately recomputed. `joint_corrections` was added to
    EMResult long after this constructor was written and nothing forced anyone
    to notice -- so the lever ran inert for months while its logs said it was
    working. A new field now fails this test until it is classified.
    """
    import dataclasses

    # Recomputed from the sessions rather than carried -- combining them IS the
    # job of this function, so a distinctive input value must NOT survive.
    RECOMPUTED = {
        "m_probs", "u_probs", "match_weights",
        "converged", "iterations", "proportion_matched",
    }
    # Internal provenance, not part of the trained model.
    INTERNAL = {"_source_schema_version"}

    fields = [f.name for f in dataclasses.fields(EMResult)]
    sessions = [
        (("first_name",), _sess_em([("first_name", "surname", 2.48)], 0.9), 100.0),
        (("surname",), _sess_em([("first_name", "surname", 2.48)], 0.9), 100.0),
    ]
    sessions[0][1].tf_freqs = {"first_name": {"smith": 0.1}}
    sessions[1][1].tf_freqs = {"first_name": {"smith": 0.1}}
    sessions[0][1].tf_collision = {"first_name": 0.01}
    sessions[1][1].tf_collision = {"first_name": 0.01}
    sessions[0][1].training_config = {"marker": True}
    sessions[1][1].training_config = {"marker": True}

    out = P._combine_em_sessions(_mk(), sessions)
    dropped = [
        n for n in fields
        if n not in RECOMPUTED and n not in INTERNAL
        and getattr(sessions[0][1], n) is not None
        and getattr(out, n) is None
    ]
    assert not dropped, (
        f"_combine_em_sessions drops {dropped} -- carry them, or add them to "
        f"RECOMPUTED/INTERNAL with a reason. This is the bug that made the "
        f"field-dependence correction inert."
    )


# ---------------------------------------------------------------------------
# Every native route must consult the model, not just the matchkey
#
# `_fs_native_eligible(mk)` answers a question about the MATCHKEY, so it cannot
# see a post-adjustment carried on the trained model. The decline was taught to
# ONE of four routes; the bucket kernel, the bucket-batch path and the
# out-of-core path each gated on it alone and scored natively anyway. That is
# why the correction stayed inert on the panel even after it was carried
# through the EM combine: estimated, logged, carried, then bypassed by the
# scorer that actually ran.
# ---------------------------------------------------------------------------

def test_native_route_declines_when_a_correction_is_present():
    em = _sess_em([("first_name", "surname", 2.48)])
    assert P._fs_native_route_eligible(_mk(), em) is False


def test_native_route_defers_to_matchkey_eligibility_without_corrections(monkeypatch):
    """KNOWN-POSITIVE: with no correction the wrapper must be transparent, or
    it would silently disable the native kernel for everyone."""
    monkeypatch.setattr(P, "_fs_native_eligible", lambda mk: True)
    assert P._fs_native_route_eligible(_mk(), _sess_em(None)) is True
    monkeypatch.setattr(P, "_fs_native_eligible", lambda mk: False)
    assert P._fs_native_route_eligible(_mk(), _sess_em(None)) is False


def test_no_routing_decision_calls_the_matchkey_only_check_directly():
    """THE CLASS GUARD.

    A route that asks `_fs_native_eligible(mk)` cannot know a post-adjustment
    exists. Exactly one place is allowed to call it: the wrapper that adds the
    model half. A new scoring route calling it directly fails here rather than
    silently discarding whatever the model carries.
    """
    import re
    from pathlib import Path

    root = Path(P.__file__).resolve().parent.parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"(?<!_route)_fs_native_eligible\(", line):
                continue
            if line.lstrip().startswith(("#", "*", '"')) or "``" in line:
                continue          # prose, not a call
            if "def _fs_native_eligible(" in line:
                continue          # the definition itself
            if "return _fs_native_eligible(mk)" in line:
                continue          # the ONE allowed call, inside the wrapper
            offenders.append(f"{path.relative_to(root)}:{i}: {line.strip()}")
    assert not offenders, (
        "these call the matchkey-only check directly and so cannot see a "
        "model-carried post-adjustment; route them through "
        "_fs_native_route_eligible(mk, em_result):\n  " + "\n  ".join(offenders)
    )
