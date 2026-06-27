from __future__ import annotations

from erkgbench.qa_e2e.corpora import Document
from erkgbench.qa_e2e.temporal import (
    TemporalResult,
    as_of_accuracy,
    gate_verdicts,
    render_temporal_md,
    temporal_blind_floor,
)


def _docs():
    return (
        Document(id="x::works_at::a::t1", text="As of 1, X works at Apple.",
                 src_surface="X", dst_surface="Apple"),
        Document(id="x::works_at::b::t5", text="From 5, X works at Google.",
                 src_surface="X", dst_surface="Google"),
    )


def test_floor_returns_latest_object_ignoring_D():
    docs = _docs()
    s2c = {"X": "x", "Apple": "a", "Google": "b"}
    # asked about a PAST date (D=3, before the correction) -> floor STILL returns latest (b)
    got = temporal_blind_floor(docs, {"X"}, "works_at", D=3, surface_to_canon=s2c)
    assert got == "b"  # wrong for the past regime (gold would be 'a')


def test_as_of_accuracy():
    assert as_of_accuracy("a", "a") == 1.0
    assert as_of_accuracy("b", "a") == 0.0
    assert as_of_accuracy(None, "a") == 0.0


def test_gate_verdicts_pass_when_gg_high_and_beats_floor_on_past():
    gg = {"past": 1.0, "current": 1.0}
    floor = {"past": 0.0, "current": 1.0}
    v = gate_verdicts(gg, floor)
    assert all(p for _l, p, _h in v)


def test_gate_verdicts_fail_when_floor_matches_gg_on_past():
    gg = {"past": 1.0, "current": 1.0}
    floor = {"past": 1.0, "current": 1.0}  # floor somehow right on past -> no capability gap
    v = gate_verdicts(gg, floor)
    gap = next(p for label, p, _h in v if "PAST" in label)
    assert gap is False


def test_render_has_regimes_and_verdicts():
    res = TemporalResult(gg_acc={"past": 1.0, "current": 1.0},
                         floor_acc={"past": 0.0, "current": 1.0}, llm_acc=None)
    md = render_temporal_md(res)
    assert "past" in md and "current" in md and ("PASS" in md or "FAIL" in md)
