"""Issue #1805 (checkbox 4) — verify the moderate-scale FS route-parity
mechanism (`bench_fs_route_parity`) at SMALL scale.

The scheduled lane runs `check_route_parity` at 10-50k rows (too slow for the
per-PR suite). This exercises the SAME code path at a few hundred rows so the
parity mechanism itself (EM pinning + arrow/polars membership compare) is
proven in the normal suite, and unit-tests the pure `membership` comparator.
Skips when polars is absent (the polars lane needs it).
"""
import importlib.util
import os
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "bench_fs_route_parity",
    pathlib.Path(__file__).parent.parent / "scripts" / "bench_fs_route_parity.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HAS_POLARS = importlib.util.find_spec("polars") is not None


def test_membership_comparator_ignores_singletons_and_canonicalizes():
    clusters = {
        7: {"members": [3, 1, 2]},   # multi-member -> kept, order-independent
        8: {"members": [9]},          # singleton -> dropped
        9: {"members": []},           # empty -> dropped
    }
    assert _mod.membership(clusters) == frozenset({frozenset({1, 2, 3})})


@pytest.mark.skipif(not _HAS_POLARS, reason="polars lane requires the optional polars dependency")
def test_route_parity_holds_at_small_scale():
    # Save/restore the env check_route_parity mutates directly (CI hygiene:
    # a leaked GOLDENMATCH_FRAME=polars would poison later tests).
    saved = {k: os.environ.get(k) for k in ("GOLDENMATCH_FRAME", "GOLDENMATCH_FS_NATIVE")}
    try:
        ok, detail = _mod.check_route_parity(rows=400, dup_frac=0.2, seed=7)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert ok, detail
    assert "arrow==polars" in detail


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
