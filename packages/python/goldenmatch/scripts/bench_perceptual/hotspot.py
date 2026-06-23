"""cProfile hotspot analysis for the perceptual kernels.

Reports the functions that dominate **self-time** (``tottime``) for a unit of
work, so a slow path can be localized to a specific function rather than a whole
stage.

Two caveats, both from the repo's performance-audit lesson:

- It profiles the **pure-Python path** (``GOLDENMATCH_NATIVE=0``). cProfile only
  sees Python frames; the native Rust kernel is opaque to it. That is the point:
  on a pure-Python deployment the wall lives in these Python frames, and that is
  exactly what a hotspot list should localize. The native path's cost is measured
  by ``perf.py`` (wall + speedup), not here.
- ``cumtime`` is NOT wall-clock (it double-counts under recursion and is blind to
  threads / native calls). ``tottime`` (self time) is the primary sort key;
  ``cumtime`` is reported alongside for context only.
"""
from __future__ import annotations

import cProfile
import os
import pstats


def _short(func: tuple) -> str:
    """``(filename, lineno, name)`` -> ``name (basename:line)``."""
    filename, lineno, name = func
    base = filename.rsplit("/", 1)[-1] if filename else "~"
    return f"{name} ({base}:{lineno})"


def profile_top(work, top_n: int = 12) -> list[dict]:
    """Run ``work`` under cProfile (Python path) and return the ``top_n`` functions
    by self-time as ``{func, ncalls, tottime, cumtime}`` dicts."""
    prev = os.environ.get("GOLDENMATCH_NATIVE")
    os.environ["GOLDENMATCH_NATIVE"] = "0"  # native kernel is opaque to cProfile
    pr = cProfile.Profile()
    try:
        pr.enable()
        work()
        pr.disable()
    finally:
        if prev is None:
            os.environ.pop("GOLDENMATCH_NATIVE", None)
        else:
            os.environ["GOLDENMATCH_NATIVE"] = prev

    stats = pstats.Stats(pr)
    rows = [
        {
            "func": _short(func),
            "ncalls": nc,
            "tottime": round(tt, 6),
            "cumtime": round(ct, 6),
        }
        for func, (cc, nc, tt, ct, _callers) in stats.stats.items()  # type: ignore[attr-defined]
    ]
    rows.sort(key=lambda r: r["tottime"], reverse=True)
    return rows[:top_n]
