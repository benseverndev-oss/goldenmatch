"""#957 ResourceBudget-backpressure follow-up: the opt-in Ray Data object-store
reservation knob (GOLDENMATCH_DISTRIBUTED_OP_RESERVATION) sets the DataContext
when present and is a no-op when unset. Ray-gated (needs ray.data.DataContext).
"""
from __future__ import annotations

import pytest

ray = pytest.importorskip("ray")


def _ctx_or_skip():
    from ray.data import DataContext

    ctx = DataContext.get_current()
    if not hasattr(ctx, "op_resource_reservation_ratio"):
        pytest.skip("this Ray lacks DataContext.op_resource_reservation_ratio")
    return ctx


def test_op_reservation_knob_sets_datacontext(monkeypatch):
    import goldenmatch.distributed.scoring as S

    ctx = _ctx_or_skip()
    orig = ctx.op_resource_reservation_ratio
    # The knob is read at import time into a module constant; patch the constant.
    monkeypatch.setattr(S, "_OP_RESERVATION", "0.2")
    try:
        S._apply_ray_data_resource_tuning()
        assert ctx.op_resource_reservation_ratio == pytest.approx(0.2)
    finally:
        ctx.op_resource_reservation_ratio = orig


def test_op_reservation_knob_noop_when_unset(monkeypatch):
    import goldenmatch.distributed.scoring as S

    ctx = _ctx_or_skip()
    monkeypatch.setattr(S, "_OP_RESERVATION", None)
    orig = ctx.op_resource_reservation_ratio
    S._apply_ray_data_resource_tuning()
    assert ctx.op_resource_reservation_ratio == orig  # unchanged


def test_op_reservation_knob_clamps_to_unit_interval(monkeypatch):
    import goldenmatch.distributed.scoring as S

    ctx = _ctx_or_skip()
    orig = ctx.op_resource_reservation_ratio
    monkeypatch.setattr(S, "_OP_RESERVATION", "5")  # out of range -> clamp to 1.0
    try:
        S._apply_ray_data_resource_tuning()
        assert ctx.op_resource_reservation_ratio == pytest.approx(1.0)
    finally:
        ctx.op_resource_reservation_ratio = orig
