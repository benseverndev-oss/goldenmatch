from __future__ import annotations

from erkgbench.qa_e2e.aggregation import (
    AggregationResult,
    gate_verdicts,
    passage_window_floor,
    render_aggregation_md,
)
from erkgbench.qa_e2e.corpora import Document


def _docs(anchor_surface, member_surfaces):
    return tuple(
        Document(id=f"gm:a::rel::gm:m{i}", text=f"{anchor_surface} rel {m}.",
                 src_surface=anchor_surface, dst_surface=m)
        for i, m in enumerate(member_surfaces)
    )


def test_floor_recall_capped_by_window():
    members = [f"M{i}" for i in range(30)]
    docs = _docs("Acme", members)
    universe = {f"M{i}": f"gm:m{i}" for i in range(30)}
    got = passage_window_floor(docs, {"Acme"}, "rel", passage_k=10,
                               surface_to_canon=universe)
    assert len(got) <= 10  # only 10 docs in the window


def test_floor_full_when_window_covers_set():
    members = [f"M{i}" for i in range(5)]
    docs = _docs("Acme", members)
    universe = {f"M{i}": f"gm:m{i}" for i in range(5)}
    got = passage_window_floor(docs, {"Acme"}, "rel", passage_k=10,
                               surface_to_canon=universe)
    assert got == {f"gm:m{i}" for i in range(5)}


def test_gate_verdicts_widening_gap_passes():
    gg = {"2-4": 1.0, "11-20": 1.0}
    floor = {"2-4": 0.9, "11-20": 0.3}
    v = gate_verdicts(gg, floor, gg_threshold=0.9)
    assert all(passed for _l, passed, _hard in v)


def test_gate_verdicts_flat_gap_fails_hard():
    gg = {"2-4": 1.0, "11-20": 1.0}
    floor = {"2-4": 0.5, "11-20": 0.5}
    v = gate_verdicts(gg, floor, gg_threshold=0.9)
    widen = next(p for label, p, _h in v if "widen" in label.lower())
    assert widen is False


def test_render_has_buckets_and_verdicts():
    res = AggregationResult(
        gg_setf1={"2-4": 1.0, "11-20": 1.0},
        floor_setf1={"2-4": 0.9, "11-20": 0.3},
        gg_count_acc={"2-4": 1.0, "11-20": 1.0},
        llm_setf1=None,
    )
    md = render_aggregation_md(res)
    assert "2-4" in md and "11-20" in md and ("PASS" in md or "FAIL" in md)
