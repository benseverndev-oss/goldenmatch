"""P2a: `_truncate_plan` unit tests. NO pyspark needed.

Deliberately not in test_sail_clustering_parity.py -- that file is gated on
`importorskip("pyspark")`, and a regression test for a pure-Python helper should
not be invisible in every lane without a Spark install. The bug these pin broke
every WCC test on pysail and would have been caught earlier had it run here.
"""
from __future__ import annotations


def test_truncate_plan_is_a_noop_when_the_backend_lacks_the_primitive():
    """P2a regression: `_truncate_plan` must survive a backend that raises from
    ATTRIBUTE ACCESS, not just from the call.

    pyspark's Spark Connect DataFrame raises PySparkNotImplementedError out of
    __getattr__ for unsupported methods (`checkpoint` and `localCheckpoint` are
    both on that list under pyspark 3.5 Connect). That is not AttributeError, so
    `getattr(df, name, None)` does NOT return the default -- the first version of
    this helper put getattr outside the try and broke every WCC test on pysail.
    """
    from goldenmatch.spark.clustering import _truncate_plan

    class _RaisesOnAttributeAccess:
        def __getattr__(self, name):
            if name in ("checkpoint", "localCheckpoint"):
                raise NotImplementedError(f"[NOT_IMPLEMENTED] {name}() is not implemented.")
            raise AttributeError(name)

    df = _RaisesOnAttributeAccess()
    assert _truncate_plan(df) is df, "must return the frame unchanged, not raise"


def test_truncate_plan_uses_local_checkpoint_when_available():
    """And when the primitive IS there, it must actually be used -- a helper that
    silently no-ops everywhere would 'fix' nothing while looking correct."""
    from goldenmatch.spark.clustering import _truncate_plan

    sentinel = object()
    calls: list[str] = []

    class _Supports:
        def localCheckpoint(self, eager=False):  # noqa: N802 - pyspark's spelling
            calls.append(f"localCheckpoint(eager={eager})")
            return sentinel

    assert _truncate_plan(_Supports()) is sentinel
    assert calls == ["localCheckpoint(eager=True)"], calls
