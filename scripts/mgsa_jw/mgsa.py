"""MGSA-JW prototype -- Bimodal Gaussian Convolutional Jaro-Winkler.

PROTOTYPE. Deliberately NOT registered as a goldenmatch scorer: nothing here
touches ``VALID_SCORERS`` or ``parity/goldenmatch.yaml``, so the parity gate and
its coverage floors do not fire. The point of this module is to produce a
MEASUREMENT that decides whether the algorithm earns the real port (Rust kernel
in ``goldenfuzz-core`` + Python fallback + TS + parity entry + conformance
fixtures). See ``bench.py`` for the comparison harness.

Sources
-------
ConvJ / ConvJW: Rozinek & Mares, "Fast and Precise Convolutional Jaro and
Jaro-Winkler Similarity", FRUCT 35 (2024). Equations (10)-(20), Algorithms 3-4.

MGSA-JW: Rozinek, "A Bimodal Gaussian Convolutional Jaro-Winkler Similarity for
Token-Aware Entity Matching", ADBIS 2026 (submission 753067).

What the papers pin down, and what this module had to decide
------------------------------------------------------------
Pinned by FRUCT 35: the Gaussian kernel (10), the window half-width
w = ceil(3.29*sigma) (15), the three-term aggregation (20), and the Winkler
prefix (Algorithm 4).

Pinned by the ADBIS abstract: two coexisting Gaussian peaks per character (the
standard positional peak plus a token-aware peak whose centre comes from a
greedy one-to-one token alignment), the ``Q^p`` gate on the token peak, a greedy
one-to-one assignment over candidate ``(i, j)`` pairs bounding matched mass by
``min(n, m)``, and "ConvJW aggregation and Winkler prefix inherited unchanged".

DECIDED HERE (the abstract does not specify; each is a knob so the bench can
measure the choice rather than assume it):

1. ``Q`` is the token-pair quality. Default ``jaro_winkler``; ``convj`` also
   available. Anything mapping two tokens to [0, 1] would satisfy the abstract.
2. Misalignment (``A_w``, eq. 18). Classical Jaro's third term is "what
   fraction of matched mass sits where it belongs". With a token peak, "where it
   belongs" is the SHIFTED centre -- otherwise the token peak would win mass in
   the first two terms and immediately give it back in the third, and a clean
   token swap could never score high. ``misalign_vs_peak=True`` (default) scores
   a match as aligned when it lands on whichever peak produced it;
   ``False`` reproduces the literal ``i == j`` reading of eq. (17).
3. FRUCT eq. (18)/Algorithm 3 disagree with each other on ``A_w`` (the
   pseudocode reads a leaked loop variable ``j`` after the loop ends). This
   module follows eq. (18): a matched character is misaligned when it is not at
   its expected centre.

A note on the published ConvJ, which is why ``convj`` is not the thing to port
--------------------------------------------------------------------------
``convj`` is faithful to Algorithm 3, INCLUDING its per-character ``max`` with
no one-to-one constraint. That lets several characters of ``S1`` claim the same
character of ``S2``, so ``M_w`` is bounded by ``|S1|`` but not by ``|S2|``, and
``M_w / |S2|`` -- the second term of eq. (20) -- can exceed 1. ``convj("aa",
"a")`` returns > 1 as published. ``clamp=True`` (the default) clips the return
to [0, 1] so the value is usable as a scorer; ``clamp=False`` exposes the raw
score, and ``test_mgsa.py`` pins the unclamped overflow so the flaw stays
visible. MGSA-JW's one-to-one assignment removes it structurally.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# ── ConvJ / ConvJW (FRUCT 35, 2024) ──────────────────────────────────────────

_Z_999 = 3.29  # z-score for 99.9% normal coverage, eq. (14)


def _window(sigma: float) -> int:
    """Half-window w = ceil(3.29 * sigma), eq. (15)."""
    return max(1, math.ceil(_Z_999 * sigma))


def _gauss_table(w: int, sigma: float) -> list[float]:
    """G[d] = exp(-d^2 / 2*sigma^2) for d in [0, w], eq. (10)."""
    two_var = 2.0 * sigma * sigma
    return [math.exp(-(d * d) / two_var) for d in range(w + 1)]


def _winkler(score: float, s1: str, s2: str, weight: float, max_prefix: int) -> float:
    """s + l*p*(1 - s), with l the common prefix capped at max_prefix."""
    limit = min(len(s1), len(s2), max_prefix)
    common = 0
    for i in range(limit):
        if s1[i] != s2[i]:
            break
        common += 1
    return score + common * weight * (1.0 - score)


def convj(s1: str, s2: str, sigma: float = 2.0, *, clamp: bool = True) -> float:
    """Convolutional Jaro, faithful to FRUCT 35 Algorithm 3 / eq. (20).

    Set ``clamp=False`` to observe the unbounded-``M_w`` overflow described in
    the module docstring.
    """
    n, m = len(s1), len(s2)
    if n == 0 or m == 0:
        return 1.0 if n == m else 0.0

    w = _window(sigma)
    g = _gauss_table(w, sigma)

    m_w = 0.0
    a_w = 0.0
    for i in range(n):
        best = 0.0
        best_j = -1
        for j in range(max(0, i - w), min(m, i + w + 1)):
            if s1[i] == s2[j]:
                weight = g[abs(i - j)]
                if weight > best:
                    best, best_j = weight, j
                if weight == 1.0:
                    break  # Algorithm 3 early termination
        m_w += best
        if best > 0.0 and best_j != i:
            a_w += best  # eq. (18): matched, but not at the identical index

    if m_w == 0.0:
        return 0.0
    score = (m_w / n + m_w / m + (m_w - a_w) / m_w) / 3.0
    return min(1.0, max(0.0, score)) if clamp else score


def convjw(
    s1: str,
    s2: str,
    sigma: float = 2.0,
    *,
    prefix_weight: float = 0.1,
    max_prefix: int = 4,
) -> float:
    """Convolutional Jaro-Winkler, FRUCT 35 Algorithm 4."""
    return _winkler(convj(s1, s2, sigma), s1, s2, prefix_weight, max_prefix)


# ── MGSA-JW (ADBIS 2026) ─────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[0-9A-Za-z]+")


@dataclass(frozen=True)
class Token:
    text: str
    start: int  # char offset in the source string


def _tokenize(s: str) -> list[Token]:
    return [Token(mt.group(), mt.start()) for mt in _TOKEN_RE.finditer(s)]


def _token_quality(a: str, b: str, measure: str, sigma: float) -> float:
    if measure == "convj":
        return convj(a, b, sigma)
    if measure == "jaro_winkler":
        # Local import: goldenmatch is the baseline provider, not a hard
        # dependency of the algorithm itself.
        from goldenmatch.core import strsim

        return float(strsim.jaro_winkler_similarity(a, b))
    raise ValueError(f"unknown token quality measure: {measure!r}")


def _align_tokens(
    t1: list[Token],
    t2: list[Token],
    *,
    measure: str,
    sigma: float,
    q_floor: float,
) -> dict[int, tuple[int, float]]:
    """Greedy one-to-one token alignment, best quality first.

    Returns ``{index into t1: (index into t2, Q)}``. Greedy rather than optimal
    (Hungarian) because the abstract specifies greedy; token counts are small
    enough that the difference is rarely material, and an exact assignment is
    one of the things a Rust port could revisit.
    """
    cands: list[tuple[float, int, int]] = []
    for ia, ta in enumerate(t1):
        for ib, tb in enumerate(t2):
            q = _token_quality(ta.text, tb.text, measure, sigma)
            if q >= q_floor:
                cands.append((q, ia, ib))
    # Sort by quality desc; (ia, ib) tie-break keeps the result deterministic,
    # which matters because this feeds a conformance-tested kernel later.
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))

    used_a: set[int] = set()
    used_b: set[int] = set()
    out: dict[int, tuple[int, float]] = {}
    for q, ia, ib in cands:
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia)
        used_b.add(ib)
        out[ia] = (ib, q)
    return out


def _char_shifts(
    s1: str,
    s2: str,
    *,
    measure: str,
    sigma: float,
    q_floor: float,
    p: float,
) -> tuple[list[int | None], list[float]]:
    """Per-character token-peak centre offset and its ``Q^p`` gate.

    ``shift[i]`` is the delta to add to ``i`` to reach the token-aware peak, or
    None when character ``i`` is outside any aligned token.
    """
    t1, t2 = _tokenize(s1), _tokenize(s2)
    aligned = _align_tokens(t1, t2, measure=measure, sigma=sigma, q_floor=q_floor)

    shift: list[int | None] = [None] * len(s1)
    gate: list[float] = [0.0] * len(s1)
    for ia, (ib, q) in aligned.items():
        ta, tb = t1[ia], t2[ib]
        delta = tb.start - ta.start
        g = q**p
        for off in range(len(ta.text)):
            i = ta.start + off
            shift[i] = delta
            gate[i] = g
    return shift, gate


def mgsa_jw(
    s1: str,
    s2: str,
    sigma: float = 2.0,
    *,
    p: float = 2.0,
    token_quality: str = "jaro_winkler",
    q_floor: float = 0.0,
    prefix_weight: float = 0.1,
    max_prefix: int = 4,
    misalign_vs_peak: bool = True,
    token_peak: bool = True,
) -> float:
    """Bimodal Gaussian Convolutional Jaro-Winkler.

    Two coexisting Gaussian peaks per character of ``s1``: the standard ConvJW
    peak centred at ``j = i``, and a token-aware peak centred at ``j = i + delta``
    where ``delta`` comes from the greedy token alignment, scaled by ``Q^p``. A
    greedy one-to-one assignment over candidate ``(i, j)`` pairs then bounds
    matched mass by ``min(n, m)``.

    ``token_peak=False`` disables the second peak, leaving ConvJW-with-one-to-one
    -- the ablation that isolates how much of any gain comes from the token
    alignment versus from the assignment constraint alone.
    """
    n, m = len(s1), len(s2)
    if n == 0 or m == 0:
        return 1.0 if n == m else 0.0

    w = _window(sigma)
    g = _gauss_table(w, sigma)

    if token_peak:
        shift, gate = _char_shifts(s1, s2, measure=token_quality, sigma=sigma, q_floor=q_floor, p=p)
    else:
        shift, gate = [None] * n, [0.0] * n

    def gaussian(d: int) -> float:
        return g[d] if d <= w else 0.0

    # Candidate (i, j) pairs: equal characters within reach of EITHER peak.
    # Reaching only around `i` would put the token peak outside the window and
    # silently discard the entire contribution of the token alignment.
    cands: list[tuple[float, int, int, int]] = []  # (weight, i, j, centre)
    for i in range(n):
        spans = [range(max(0, i - w), min(m, i + w + 1))]
        delta = shift[i]
        if delta is not None:
            centre = i + delta
            spans.append(range(max(0, centre - w), min(m, centre + w + 1)))

        seen: set[int] = set()
        for span in spans:
            for j in span:
                if j in seen or s1[i] != s2[j]:
                    continue
                seen.add(j)
                w_pos = gaussian(abs(i - j))
                w_tok = 0.0
                if delta is not None:
                    w_tok = gate[i] * gaussian(abs(j - (i + delta)))
                if w_tok > w_pos:
                    cands.append((w_tok, i, j, i + delta))  # type: ignore[operator]
                elif w_pos > 0.0:
                    cands.append((w_pos, i, j, i))

    cands.sort(key=lambda c: (-c[0], c[1], c[2]))

    used_i: set[int] = set()
    used_j: set[int] = set()
    m_w = 0.0
    a_w = 0.0
    for weight, i, j, centre in cands:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        m_w += weight
        expected = centre if misalign_vs_peak else i
        if j != expected:
            a_w += weight

    if m_w == 0.0:
        return 0.0
    score = (m_w / n + m_w / m + (m_w - a_w) / m_w) / 3.0
    score = min(1.0, max(0.0, score))
    return _winkler(score, s1, s2, prefix_weight, max_prefix)
