"""Vendored pure-Python string-similarity primitives — GoldenMatch's own
byte-identical implementations of the metrics the scalar scorer sites use,
replacing the ``rapidfuzz`` Python dependency on those paths.

Companion to the Rust ``score-core::strsim`` (the native fast path). These are
the DEPENDENCY-FREE fallback: correct and byte-identical to rapidfuzz, used
where the value is scalar/low-frequency (explainer, boost, schema-match, block
analysis, refdata/synonym scorers) so a plain ``pip install goldenmatch`` needs
no ``rapidfuzz``. The hot vectorized ``cdist`` path routes through the native
kernel separately (native-required fast path).

Each function matches a SPECIFIC rapidfuzz entry point BYTE-FOR-BYTE (proven by
``tests/test_strsim_parity.py`` against the ``rapidfuzz`` package):

  * ``jaro_winkler_similarity``            == ``JaroWinkler.similarity``            (RAW, no round-trip)
  * ``jaro_winkler_normalized_similarity`` == ``JaroWinkler.normalized_similarity`` (Metricf64 round-trip)
  * ``levenshtein_normalized_similarity``  == ``Levenshtein.normalized_similarity``
  * ``indel_normalized_similarity``        == ``Indel.normalized_similarity`` (== ``fuzz.ratio`` on [0,1])
  * ``damerau_levenshtein_distance``       == ``DamerauLevenshtein.distance`` (true/unrestricted DL)

The RAW-vs-normalized distinction is load-bearing: ``JaroWinkler.similarity``
and ``.normalized_similarity`` differ by a ULP in ~15% of inputs (jaro_winkler
is a ``Metricf64`` similarity metric with ``maximum == 1.0``, so rapidfuzz's
``normalized_similarity`` returns ``1.0 - (1.0 - raw)``). Call sites keep the
exact variant they used before, so migration is byte-identical.
"""
from __future__ import annotations

__all__ = [
    "jaro_similarity",
    "jaro_winkler_similarity",
    "jaro_winkler_normalized_similarity",
    "levenshtein_distance",
    "levenshtein_normalized_similarity",
    "indel_normalized_similarity",
    "ratio",
    "token_sort_ratio",
    "partial_ratio",
    "damerau_levenshtein_distance",
    "pure_field_matrix",
]


# ---------------------------------------------------------------------------
# Jaro / Jaro-Winkler
# ---------------------------------------------------------------------------


def jaro_similarity(s1: str, s2: str) -> float:
    """Jaro similarity on ``[0, 1]`` (== rapidfuzz ``Jaro.similarity``)."""
    len1 = len(s1)
    len2 = len(s2)
    if len1 == 0 and len2 == 0:
        return 1.0
    # length_filter: either side empty -> 0.0 at any cutoff.
    if len1 == 0 or len2 == 0:
        return 0.0
    if len1 == 1 and len2 == 1:
        return 1.0 if s1 == s2 else 0.0

    # rapidfuzz bound = max(len1, len2) // 2 - 1 (both >= 1 here, so >= 0).
    bound = max(len1, len2) // 2 - 1

    s1_flag = [False] * len1
    s2_flag = [False] * len2
    common = 0
    # Iterate the TEXT (s2) positions; bind each to the LOWEST unflagged
    # PATTERN (s1) position within [j-bound, j+bound] (rapidfuzz's blsi order).
    for j in range(len2):
        lo = j - bound if j > bound else 0
        hi = j + bound
        if hi > len1 - 1:
            hi = len1 - 1
        cj = s2[j]
        i = lo
        while i <= hi:
            if not s1_flag[i] and s1[i] == cj:
                s1_flag[i] = True
                s2_flag[j] = True
                common += 1
                break
            i += 1

    if common == 0:
        return 0.0

    # Transpositions: pair the k-th flagged pattern char with the k-th flagged
    # text char; count positional disagreements.
    transposition = 0
    k = 0
    for i in range(len1):
        if s1_flag[i]:
            while not s2_flag[k]:
                k += 1
            if s1[i] != s2[k]:
                transposition += 1
            k += 1

    transposition //= 2
    sim = 0.0
    sim += common / len1
    sim += common / len2
    sim += (common - transposition) / common
    return sim / 3.0


def _jaro_winkler_raw(s1: str, s2: str) -> float:
    """Jaro-Winkler with the default 0.1 prefix weight, RAW (no round-trip).

    == rapidfuzz ``JaroWinkler.similarity``.
    """
    prefix = 0
    for c1, c2 in zip(s1[:4], s2[:4]):
        if c1 == c2:
            prefix += 1
        else:
            break
    sim = jaro_similarity(s1, s2)
    if sim > 0.7:
        sim += prefix * 0.1 * (1.0 - sim)
    return sim


def jaro_winkler_similarity(s1: str, s2: str) -> float:
    """== rapidfuzz ``JaroWinkler.similarity`` (RAW jaro-winkler, no round-trip)."""
    return _jaro_winkler_raw(s1, s2)


def jaro_winkler_normalized_similarity(s1: str, s2: str) -> float:
    """== rapidfuzz ``JaroWinkler.normalized_similarity``.

    jaro_winkler is a ``Metricf64`` similarity metric with ``maximum == 1.0``,
    so ``normalized_similarity`` is ``1.0 - (1.0 - raw)`` (the division by the
    1.0 maximum is exact). That double round-trip shifts the last ULP vs the raw
    value; replicated for byte-identical output. This is what the native
    ``jaro_winkler_similarity`` kernel returns.
    """
    return 1.0 - (1.0 - _jaro_winkler_raw(s1, s2))


# ---------------------------------------------------------------------------
# Levenshtein
# ---------------------------------------------------------------------------


def levenshtein_distance(s1: str, s2: str) -> int:
    """Uniform-weight Levenshtein edit distance (Wagner-Fischer, two-row)."""
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    cur = [0] * (len(s2) + 1)
    for i, ca in enumerate(s1):
        cur[0] = i + 1
        for j, cb in enumerate(s2):
            cost = 0 if ca == cb else 1
            d = prev[j] + cost
            ins = cur[j] + 1
            if ins < d:
                d = ins
            dele = prev[j + 1] + 1
            if dele < d:
                d = dele
            cur[j + 1] = d
        prev, cur = cur, prev
    return prev[len(s2)]


def levenshtein_normalized_similarity(s1: str, s2: str) -> float:
    """== rapidfuzz ``Levenshtein.normalized_similarity``:
    ``1.0 - dist / max(len1, len2)`` (``maximum == 0 -> 1.0``)."""
    maximum = max(len(s1), len(s2))
    if maximum == 0:
        return 1.0
    return 1.0 - (levenshtein_distance(s1, s2) / maximum)


# ---------------------------------------------------------------------------
# Indel / fuzz.ratio  (LCS-based, substitution-free edit distance)
# ---------------------------------------------------------------------------


def _lcs_length(s1: str, s2: str) -> int:
    if not s1 or not s2:
        return 0
    prev = [0] * (len(s2) + 1)
    cur = [0] * (len(s2) + 1)
    for ca in s1:
        for j, cb in enumerate(s2):
            if ca == cb:
                cur[j + 1] = prev[j] + 1
            else:
                a = cur[j]
                b = prev[j + 1]
                cur[j + 1] = a if a > b else b
        prev, cur = cur, prev
    return prev[len(s2)]


def indel_normalized_similarity(s1: str, s2: str) -> float:
    """== rapidfuzz ``Indel.normalized_similarity`` (== ``fuzz.ratio`` on [0,1]):
    indel distance ``= len1 + len2 - 2*lcs``, ``sim = 1.0 - dist / (len1+len2)``
    (``maximum == 0 -> 1.0``)."""
    maximum = len(s1) + len(s2)
    if maximum == 0:
        return 1.0
    dist = maximum - 2 * _lcs_length(s1, s2)
    return 1.0 - (dist / maximum)


def ratio(s1: str, s2: str) -> float:
    """== rapidfuzz ``fuzz.ratio`` (0-100 scale): the Indel normalized similarity
    scaled to [0, 100]. Byte-identical to the crate (proven in the strsim parity
    test's realistic corpus)."""
    return indel_normalized_similarity(s1, s2) * 100.0


def token_sort_ratio(s1: str, s2: str) -> float:
    """== rapidfuzz ``fuzz.token_sort_ratio`` (0-100 scale): Indel ratio over
    whitespace-split, code-point-sorted, space-joined tokens. No case
    normalization (rapidfuzz's default processor here is a no-op), so
    ``Foo``/``foo`` scores 66.67, matching the crate."""
    a = " ".join(sorted(s1.split()))
    b = " ".join(sorted(s2.split()))
    return indel_normalized_similarity(a, b) * 100.0


def partial_ratio(s1: str, s2: str) -> float:
    """Best Indel ratio of the shorter string against any (edge-overhang)
    window of the longer, on the 0-100 scale — the substring-tolerant matcher
    ``rapidfuzz.fuzz.partial_ratio`` provides.

    FAITHFUL, not byte-identical: this brute-forces every alignment offset (incl.
    partial edge windows) and takes the max. It is EXACT on substring-containment
    (one string contained in the other -> 100), which is the signal its ONLY
    consumer — ``core/schema_match.py``'s advisory column-name-mapping heuristic
    (thresholded, 0.9-discounted) — actually keys on. On non-containment pairs it
    can differ from rapidfuzz by up to ~14 points (rapidfuzz uses a local
    block-alignment this doesn't replicate; mean abs error ~0.1 over a column
    corpus), which is immaterial to an advisory suggestion score. `partial_ratio`
    is Python-rapidfuzz-only (not in rapidfuzz-rs / score-core), so there is no
    native kernel to defer to.
    """
    if not s1 and not s2:
        return 100.0
    if not s1 or not s2:
        return 0.0
    shorter, longer = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    ls, ll = len(shorter), len(longer)
    best = 0.0
    for start in range(-(ls - 1), ll):
        lo = start if start > 0 else 0
        hi = start + ls
        if hi > ll:
            hi = ll
        if lo >= hi:
            continue
        r = indel_normalized_similarity(shorter, longer[lo:hi]) * 100.0
        if r > best:
            best = r
    return best


# Field-scorer name -> (primitive, output scale) for the dense-matrix builder.
# jaro_winkler/levenshtein are on [0,1]; token_sort is on [0,100] (callers
# divide by 100, exactly as with the old rapidfuzz cdist).
_MATRIX_SCORERS = {
    "jaro_winkler": jaro_winkler_similarity,
    "levenshtein": levenshtein_normalized_similarity,
    "token_sort": token_sort_ratio,
}


def pure_field_matrix(values: list, scorer_name: str, dtype: str = "float32"):
    """Dependency-free NxN field-score matrix, the fallback for the vectorized
    ``rapidfuzz.process.cdist`` path when the native kernel is absent.

    BYTE-IDENTICAL to ``cdist(values, values, scorer=<matching rapidfuzz scorer>,
    dtype=<dtype>)`` (the vendored primitive == the rapidfuzz scalar, proven in
    ``tests/test_strsim_parity.py``). Distinct values are scored once and
    gathered, so the cost is O(distinct^2), not O(N^2) — but this is the SLOW
    path (a plain ``pip install goldenmatch`` with no native wheel); the fast
    path stays the native ``score_field_matrix`` kernel.

    ``dtype`` mirrors cdist's: the bucket vec-lane needs ``"float64"`` (bit-exact
    ``>= threshold`` decisions); ``find_fuzzy_matches`` uses the default float32.
    The scorer computes in Python float (f64) and is stored at ``dtype``, exactly
    as cdist casts its f64 scalar result.
    """
    import numpy as np

    fn = _MATRIX_SCORERS[scorer_name]
    n = len(values)
    uniq: dict[str, int] = {}
    codes = np.empty(n, dtype=np.intp)
    vals: list[str] = []
    for i, v in enumerate(values):
        c = uniq.get(v)
        if c is None:
            c = len(vals)
            uniq[v] = c
            vals.append(v)
        codes[i] = c
    u = len(vals)
    sub = np.empty((u, u), dtype=dtype)
    for a in range(u):
        sub[a, a] = fn(vals[a], vals[a])
        for b in range(a + 1, u):
            s = fn(vals[a], vals[b])
            sub[a, b] = s
            sub[b, a] = s
    return sub[np.ix_(codes, codes)]


# ---------------------------------------------------------------------------
# Damerau-Levenshtein (true / unrestricted, Lowrance-Wagner)
# ---------------------------------------------------------------------------


def damerau_levenshtein_distance(s1: str, s2: str) -> int:
    """True Damerau-Levenshtein distance (adjacent transposition = 1 edit,
    unrestricted) == rapidfuzz ``DamerauLevenshtein.distance``."""
    la = len(s1)
    lb = len(s2)
    if la == 0:
        return lb
    if lb == 0:
        return la
    inf = la + lb
    w = lb + 2
    d = [0] * ((la + 2) * w)
    d[0] = inf  # d[0][0]
    for i in range(la + 1):
        d[(i + 1) * w + 0] = inf
        d[(i + 1) * w + 1] = i
    for j in range(lb + 1):
        d[0 * w + (j + 1)] = inf
        d[1 * w + (j + 1)] = j
    last: dict[str, int] = {}
    for i in range(1, la + 1):
        db = 0
        ca = s1[i - 1]
        for j in range(1, lb + 1):
            cb = s2[j - 1]
            k = last.get(cb, 0)
            ln = db
            if ca == cb:
                cost = 0
                db = j
            else:
                cost = 1
            sub = d[i * w + j] + cost
            ins = d[(i + 1) * w + j] + 1
            dele = d[i * w + (j + 1)] + 1
            trans = d[k * w + ln] + (i - k - 1) + 1 + (j - ln - 1)
            m = sub
            if ins < m:
                m = ins
            if dele < m:
                m = dele
            if trans < m:
                m = trans
            d[(i + 1) * w + (j + 1)] = m
        last[ca] = i
    return d[(la + 1) * w + (lb + 1)]
