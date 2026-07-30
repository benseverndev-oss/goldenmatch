"""Owned Hartigan dip statistic — GoldenMatch's replacement for the ``diptest``
runtime dependency.

This is a faithful, byte-identical port of Hartigan & Hartigan's dip test
(1985, Applied Statistics AS 217) as corrected by Martin Maechler in the R
``diptest`` package's ``dip.c`` -- the same algorithm the ``diptest`` PyPI wheel
compiles. It is deterministic and matches ``diptest.dipstat`` to the bit across
uniform / unimodal / multimodal / heavily-tied / degenerate inputs (the parity
test `tests/test_dip_parity.py` asserts exact agreement; ``diptest`` stays a
test-only oracle in the workspace dev group).

Why own it: ``diptest`` was a single-call runtime dependency (one
``dipstat(scores)`` in the scoring profile). Owning the algorithm drops the
compiled-wheel dependency from ``pip install goldenmatch`` and keeps the one
numeric primitive we use under our control -- "own it, don't clone it".

Scope / performance: the dip runs only on the auto-config controller's SAMPLE
iterations (``has_active_emitter()`` gates it; the full production pass does no
dip work), over the matched pairs from a ~5000-row sample -- i.e. hundreds to a
few thousand scores, where this pure-Python port is sub-20ms (and faster than
the compiled ``diptest`` at n<=1k). It is O(n log n) like the C. If a caller
ever needs the dip over very large n (>~1e5), add a native/vectorized fast path;
today the sample-sized inputs don't warrant it.
"""
from __future__ import annotations

from collections.abc import Iterable

# The ``diptest`` wheel (0.11.x) computes with ``min_is_0=True`` -- the minimum
# possible dip is 0 (not the theoretical 1/(2n) floor). Baked in so this port is
# byte-identical to the ``diptest.dipstat`` the scorer previously called.
_MIN_IS_0 = True


def hartigan_dip(values: Iterable[float]) -> float:
    """Hartigan's dip statistic of ``values`` (a 1-D sample). Returns a float in
    ``[0, 0.25]``; small means unimodal. Empty / single-value / all-equal inputs
    return ``0.0``. Byte-identical to ``diptest.dipstat``.

    The body is 1-indexed (``x[1..n]``) to mirror the reference ``dip.c``
    line-for-line; the sole owned semantic choice is sorting the input (the C
    requires pre-sorted input, which we guarantee here)."""
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0:
        return 0.0
    # Internally we carry ``2n * dip`` (Maechler's speedup: divide once at the
    # end). ``min_is_0`` picks the floor.
    dip = 0.0 if _MIN_IS_0 else 1.0
    if n < 2 or xs[-1] == xs[0]:
        return dip / (2 * n)

    x = [0.0] + xs  # x[1..n]
    mn = [0] * (n + 1)  # GCM (convex minorant) parent pointers
    mj = [0] * (n + 1)  # LCM (concave majorant) parent pointers
    gcm = [0] * (n + 1)  # GCM change points (scratch)
    lcm = [0] * (n + 1)  # LCM change points (scratch)

    # Establish the indices over which combination is necessary for the convex
    # minorant fit.
    mn[1] = 1
    for j in range(2, n + 1):
        mn[j] = j - 1
        while True:
            mnj = mn[j]
            mnmnj = mn[mnj]
            if mnj == 1 or (x[j] - x[mnj]) * (mnj - mnmnj) < (x[mnj] - x[mnmnj]) * (j - mnj):
                break
            mn[j] = mnmnj

    # ... and for the concave majorant fit.
    mj[n] = n
    for k in range(n - 1, 0, -1):
        mj[k] = k + 1
        while True:
            mjk = mj[k]
            mjmjk = mj[mjk]
            if mjk == n or (x[k] - x[mjk]) * (mjk - mjmjk) < (x[mjk] - x[mjmjk]) * (k - mjk):
                break
            mj[k] = mjmjk

    low, high = 1, n
    while True:
        # Collect the change points for the GCM from HIGH to LOW.
        gcm[1] = high
        i = 1
        while gcm[i] > low:
            gcm[i + 1] = mn[gcm[i]]
            i += 1
        ig = l_gcm = i
        ix = ig - 1  # counters for the convex minorant

        # Collect the change points for the LCM from LOW to HIGH.
        lcm[1] = low
        i = 1
        while lcm[i] < high:
            lcm[i + 1] = mj[lcm[i]]
            i += 1
        ih = l_lcm = i
        iv = 2  # counters for the concave majorant

        # Largest distance d between the GCM and the LCM.
        d = 0.0
        if l_gcm != 2 or l_lcm != 2:
            while True:
                gcmix = gcm[ix]
                lcmiv = lcm[iv]
                if gcmix > lcmiv:
                    gcmi1 = gcm[ix + 1]
                    dx = (lcmiv - gcmi1 + 1) - (x[lcmiv] - x[gcmi1]) * (gcmix - gcmi1) / (
                        x[gcmix] - x[gcmi1]
                    )
                    iv += 1
                    if dx >= d:
                        d = dx
                        ig = ix + 1
                        ih = iv - 1
                else:
                    lcmiv1 = lcm[iv - 1]
                    dx = (x[gcmix] - x[lcmiv1]) * (lcmiv - lcmiv1) / (x[lcmiv] - x[lcmiv1]) - (
                        gcmix - lcmiv1 - 1
                    )
                    ix -= 1
                    if dx >= d:
                        d = dx
                        ig = ix + 1
                        ih = iv
                if ix < 1:
                    ix = 1
                if iv > l_lcm:
                    iv = l_lcm
                if gcm[ix] == lcm[iv]:
                    break
        else:
            d = 0.0 if _MIN_IS_0 else 1.0

        if d < dip:
            break

        # The dip for the convex minorant.
        dip_l = 0.0
        for j in range(ig, l_gcm):
            max_t = 1.0
            jb = gcm[j + 1]
            je = gcm[j]
            if je - jb > 1 and x[je] != x[jb]:
                c = (je - jb) / (x[je] - x[jb])
                for jj in range(jb, je + 1):
                    t = (jj - jb + 1) - (x[jj] - x[jb]) * c
                    if max_t < t:
                        max_t = t
            if dip_l < max_t:
                dip_l = max_t

        # The dip for the concave majorant.
        dip_u = 0.0
        for j in range(ih, l_lcm):
            max_t = 1.0
            jb = lcm[j]
            je = lcm[j + 1]
            if je - jb > 1 and x[je] != x[jb]:
                c = (je - jb) / (x[je] - x[jb])
                for jj in range(jb, je + 1):
                    t = (x[jj] - x[jb]) * c - (jj - jb - 1)
                    if max_t < t:
                        max_t = t
            if dip_u < max_t:
                dip_u = max_t

        dipnew = dip_u if dip_u > dip_l else dip_l
        if dip < dipnew:
            dip = dipnew

        if low == gcm[ig] and high == lcm[ih]:
            break
        low = gcm[ig]
        high = lcm[ih]

    return dip / (2 * n)
