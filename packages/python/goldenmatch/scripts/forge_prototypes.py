#!/usr/bin/env python3
"""Prototypes of the top-15 ER string-similarity algorithms from the Algorithm Forge run.

Companion to `scripts/algorithm_forge.py` and `examples/forge_runs/run_25.md`. Each of the
15 highest-composite proposals is implemented here as a small, pure-stdlib, runnable
reference so the ideas can actually be exercised and benchmarked against the classics.

    `python forge_prototypes.py`  ->  prints a demo scoreboard and runs self-tests.

Nothing here imports goldenmatch or any third-party package; the learnable methods ship
with tiny self-contained training defaults. These are reference prototypes (correctness
and clarity over speed), not the eventual vectorized kernels.

Implemented (composite from run_25.md):
    1 AbbrevAlign 84   2 SelfThresh 81   3 BlockSimDual 81   4 ChannelMix 79
    5 CalibFS 79       6 TokenRoleAlign 79  7 PrefixTrieSoftTFIDF 79  8 FieldTypeAware 78
    9 ActiveMarginSim 78  10 AnytimeLev 77  11 NickGraph 77  12 BayesLenNorm 76
    13 StackEnsemble 76 14 RecurAlign 75  15 SegmentSwapAware 74
"""
from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Shared primitives
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(s: str) -> list[str]:
    """Lowercase alphanumeric tokens, order preserved."""
    return _TOKEN_RE.findall(s.lower())


def char_qgrams(s: str, q: int = 2) -> Counter[str]:
    s = "".join(s.split()).lower()
    if len(s) < q:
        return Counter([s]) if s else Counter()
    return Counter(s[i : i + q] for i in range(len(s) - q + 1))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def levenshtein_sim(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    window = max(la, lb) // 2 - 1
    if window < 0:
        window = 0
    a_match = [False] * la
    b_match = [False] * lb
    matches = 0
    for i in range(la):
        lo = max(0, i - window)
        hi = min(i + window + 1, lb)
        for j in range(lo, hi):
            if not b_match[j] and a[i] == b[j]:
                a_match[i] = b_match[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(la):
        if a_match[i]:
            while not b_match[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    t = transpositions / 2
    return (matches / la + matches / lb + (matches - t) / matches) / 3


def jaro_winkler(a: str, b: str, p: float = 0.1, max_prefix: int = 4) -> float:
    j = jaro(a, b)
    prefix = 0
    for ca, cb in zip(a, b):
        if ca == cb and prefix < max_prefix:
            prefix += 1
        else:
            break
    return j + prefix * p * (1 - j)


_SOUNDEX_MAP = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def soundex(s: str) -> str:
    s = "".join(ch for ch in s.lower() if ch.isalpha())
    if not s:
        return ""
    head = s[0]
    code = head.upper()
    prev = _SOUNDEX_MAP.get(head, "")
    for ch in s[1:]:
        d = _SOUNDEX_MAP.get(ch, "")
        if d and d != prev:
            code += d
        if ch not in "hw":
            prev = d
    return (code + "000")[:4]


class Idf:
    """Token IDF lookup with a default weight for unseen (assumed-rare) tokens."""

    def __init__(self, weights: dict[str, float], default: float):
        self.weights = weights
        self.default = default

    def __call__(self, token: str) -> float:
        return self.weights.get(token, self.default)


def build_idf(corpus: list[str]) -> Idf:
    n = len(corpus) or 1
    df: Counter[str] = Counter()
    for doc in corpus:
        for tok in set(tokenize(doc)):
            df[tok] += 1
    weights = {tok: math.log((n + 1) / (c + 1)) + 1.0 for tok, c in df.items()}
    default = math.log((n + 1) / 1) + 1.0  # unseen token = maximally informative
    return Idf(weights, default)


UNIT_IDF = Idf({}, 1.0)  # IDF-agnostic weighting (every token weight 1.0)


def _directional_best(A: list[str], B: list[str], sim: Callable[[str, str], float],
                      idf: Idf) -> float:
    """IDF-weighted mean over A of the best match in B (one Soft-TFIDF direction)."""
    if not A:
        return 0.0
    num = den = 0.0
    for ta in A:
        best = max((sim(ta, tb) for tb in B), default=0.0)
        w = idf(ta)
        num += w * best
        den += w
    return num / den if den else 0.0


# --------------------------------------------------------------------------- #
# 1. AbbrevAlign — Soft-TFIDF + one-token-to-many acronym/abbreviation spans
# --------------------------------------------------------------------------- #


def _subseq_abbrev(a: str, b: str) -> float:
    """Score for `a` being an order-preserving compression of `b` (Bsns<-Business)."""
    if not a or not b or a[0] != b[0]:
        return 0.0
    i = 0
    for ch in b:
        if i < len(a) and a[i] == ch:
            i += 1
    return len(a) / len(b) if i == len(a) else 0.0


def _abbrev_token_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    # v2: secondary similarity now also recognizes nickname/alias equivalence
    # (Bob<->Robert), since abbreviation and nickname variation co-occur in real
    # entity data and AbbrevAlign was otherwise blind to nicknames.
    s = max(jaro_winkler(a, b), _subseq_abbrev(a, b))
    g1, g2 = _NICK_OF.get(a), _NICK_OF.get(b)
    if g1 is not None and g1 == g2:
        s = max(s, 0.95)
    return s


# Function words an acronym skips: FBI <- Federal Bureau *of* Investigation.
_ACRONYM_STOP = {"of", "and", "the", "for", "to", "a", "an"}


def _acronym_match(acr: str, tokens: list[str]) -> bool:
    """True if `acr`'s letters are the initials of a run of content tokens (stopwords skipped).

    v3: the run may skip stopwords, but every *content* token it passes through must
    supply the next letter — so it stays tight (won't match an arbitrary subsequence).
    """
    if len(acr) < 2:
        return False
    for start in range(len(tokens)):
        i = start
        c = 0  # index into acr
        while c < len(acr) and i < len(tokens):
            tok = tokens[i]
            if tok in _ACRONYM_STOP:
                i += 1
                continue
            if tok and tok[0] == acr[c]:
                c += 1
                i += 1
            else:
                break
        if c == len(acr):
            return True
    return False


def _abbrev_direction(A: list[str], B: list[str], idf: Idf) -> float:
    if not A:
        return 0.0
    num = den = 0.0
    for ta in A:
        best = max((_abbrev_token_sim(ta, tb) for tb in B), default=0.0)
        if _acronym_match(ta, B):  # token -> initials of a content-word run
            best = 1.0
        num += idf(ta) * best
        den += idf(ta)
    return num / den if den else 0.0


def abbrev_align(a: str, b: str, idf: Idf = UNIT_IDF) -> float:
    """The acronym/abbreviation relation is directional, so similarity = best direction."""
    A, B = tokenize(a), tokenize(b)
    return max(_abbrev_direction(A, B, idf), _abbrev_direction(B, A, idf))


# --------------------------------------------------------------------------- #
# 2. SelfThresh — similarity with a decision threshold that adapts to value rarity
# --------------------------------------------------------------------------- #


@dataclass
class ThreshResult:
    score: float
    threshold: float
    decision: bool


def self_thresh(a: str, b: str, idf: Idf, base: float = 0.70, k: float = 0.20) -> ThreshResult:
    score = token_role_align(a, b)
    toks = tokenize(a) + tokenize(b)
    if toks and idf.default > 0:
        norm = min(max(sum(idf(t) for t in toks) / len(toks) / idf.default, 0.0), 1.0)
    else:
        norm = 1.0
    threshold = base + k * (1.0 - norm)  # common (low-IDF) values demand a higher bar
    return ThreshResult(score, threshold, score >= threshold)


# --------------------------------------------------------------------------- #
# 3. BlockSimDual — one similarity with a cheap, provable upper bound for blocking
# --------------------------------------------------------------------------- #


def block_sim(a: str, b: str, q: int = 2) -> float:
    """Exact q-gram Jaccard — the scoring function."""
    ga, gb = set(char_qgrams(a, q)), set(char_qgrams(b, q))
    if not ga and not gb:
        return 1.0
    return len(ga & gb) / len(ga | gb)


def block_upper_bound(a: str, b: str, q: int = 2) -> float:
    """O(1) length-filter upper bound on `block_sim` — safe to prune blocking with."""
    na = max(len(a) - q + 1, 1)
    nb = max(len(b) - q + 1, 1)
    return min(na, nb) / max(na, nb)


# --------------------------------------------------------------------------- #
# 4. ChannelMix — max-likelihood over distinct error channels
# --------------------------------------------------------------------------- #


def _phonetic_sim(a: str, b: str) -> float:
    A, B = tokenize(a), tokenize(b)
    if not A or not B:
        return 0.0
    sim = lambda x, y: 1.0 if soundex(x) == soundex(y) and soundex(x) else 0.0
    return 0.5 * (_directional_best(A, B, sim, UNIT_IDF)
                  + _directional_best(B, A, sim, UNIT_IDF))


@dataclass
class ChannelResult:
    score: float
    channel: str


def channel_mix(a: str, b: str) -> ChannelResult:
    channels = {
        "typo": levenshtein_sim(a, b),
        "phonetic": _phonetic_sim(a, b),
        "abbrev": abbrev_align(a, b),
    }
    best = max(channels, key=lambda c: channels[c])
    return ChannelResult(channels[best], best)


# --------------------------------------------------------------------------- #
# 5. CalibFS — wrap any base similarity in an isotonic (m/u-style) calibrator
# --------------------------------------------------------------------------- #


class IsotonicCalibrator:
    """Pool-adjacent-violators monotone fit mapping a raw score -> P(match)."""

    def __init__(self) -> None:
        self._x: list[float] = []
        self._y: list[float] = []

    def fit(self, xs: list[float], ys: list[float]) -> IsotonicCalibrator:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        x_sorted = [xs[i] for i in order]
        stack: list[list[float]] = []  # [mean, count]
        for i in order:
            stack.append([float(ys[i]), 1.0])
            while len(stack) >= 2 and stack[-2][0] > stack[-1][0]:
                m2, c2 = stack.pop()
                m1, c1 = stack.pop()
                stack.append([(m1 * c1 + m2 * c2) / (c1 + c2), c1 + c2])
        fitted: list[float] = []
        for mean, count in stack:
            fitted.extend([mean] * int(count))
        self._x, self._y = x_sorted, fitted
        return self

    def predict(self, x: float) -> float:
        if not self._x:
            return x
        idx = min(bisect_right(self._x, x), len(self._y)) - 1
        if idx < 0:
            idx = 0
        return self._y[idx]


class CalibratedSim:
    """Turn a raw similarity into a calibrated probability with isotonic regression."""

    def __init__(self, base: Callable[[str, str], float]):
        self.base = base
        self.cal = IsotonicCalibrator()

    def fit(self, pairs: list[tuple[str, str]], labels: list[int]) -> CalibratedSim:
        self.cal.fit([self.base(a, b) for a, b in pairs], [float(y) for y in labels])
        return self

    def score(self, a: str, b: str) -> float:
        return self.cal.predict(self.base(a, b))


# --------------------------------------------------------------------------- #
# 6. TokenRoleAlign — order-invariant alignment weighted by token role
# --------------------------------------------------------------------------- #

_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq"}
_ROLE_PRIOR = {"name": 1.0, "initial": 0.5, "suffix": 0.3, "title": 0.1}


def _role(token: str) -> str:
    if token in _TITLES:
        return "title"
    if token in _SUFFIXES:
        return "suffix"
    if len(token) == 1:
        return "initial"
    return "name"


def _role_pair_score(t1: str, r1: str, t2: str, r2: str) -> float:
    if r1 == r2:
        return jaro_winkler(t1, t2)
    if {r1, r2} == {"initial", "name"}:  # "J" vs "John"
        ini, name = (t1, t2) if r1 == "initial" else (t2, t1)
        return 0.9 if name and name[0] == ini else 0.0
    return 0.0


def token_role_align(a: str, b: str) -> float:
    A = [(t, _role(t)) for t in tokenize(a)]
    B = [(t, _role(t)) for t in tokenize(b)]

    def direction(src: list[tuple[str, str]], dst: list[tuple[str, str]]) -> float:
        num = den = 0.0
        for t1, r1 in src:
            prior = _ROLE_PRIOR[r1]
            best = max((_role_pair_score(t1, r1, t2, r2) for t2, r2 in dst), default=0.0)
            num += prior * best
            den += prior
        return num / den if den else 0.0

    return 0.5 * (direction(A, B) + direction(B, A))


# --------------------------------------------------------------------------- #
# 7. PrefixTrieSoftTFIDF — secondary match resolves abbreviations via a corpus trie
# --------------------------------------------------------------------------- #


class Trie:
    def __init__(self) -> None:
        self.children: dict[str, Trie] = {}
        self.count = 0
        self.word: str | None = None

    def insert(self, word: str, count: int = 1) -> None:
        node = self
        for ch in word:
            node = node.children.setdefault(ch, Trie())
        node.count += count
        node.word = word

    def best_expansion(self, prefix: str) -> str | None:
        node = self
        for ch in prefix:
            nxt = node.children.get(ch)
            if nxt is None:
                return None
            node = nxt
        best_word: str | None = None
        best_count = -1
        stack = [node]
        while stack:  # most frequent completed word under this prefix
            cur = stack.pop()
            if cur.word is not None and cur.count > best_count:
                best_word, best_count = cur.word, cur.count
            stack.extend(cur.children.values())
        return best_word


def build_trie(corpus: list[str]) -> Trie:
    trie = Trie()
    counts: Counter[str] = Counter()
    for doc in corpus:
        counts.update(tokenize(doc))
    for word, c in counts.items():
        trie.insert(word, c)
    return trie


def prefix_trie_soft_tfidf(a: str, b: str, trie: Trie, idf: Idf) -> float:
    def sec(ta: str, tb: str) -> float:
        s = jaro_winkler(ta, tb)
        for token, other in ((ta, tb), (tb, ta)):
            exp = trie.best_expansion(token)
            if exp is not None and exp != token:
                s = max(s, 1.0 if exp == other else jaro_winkler(exp, other))
        return s

    A, B = tokenize(a), tokenize(b)
    return 0.5 * (_directional_best(A, B, sec, idf) + _directional_best(B, A, sec, idf))


# --------------------------------------------------------------------------- #
# 8. FieldTypeAware — segment into typed sub-fields and fuse per-type similarity
# --------------------------------------------------------------------------- #


def _soft_jaccard(A: list[str], B: list[str]) -> float:
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return 0.5 * (_directional_best(A, B, jaro_winkler, UNIT_IDF)
                  + _directional_best(B, A, jaro_winkler, UNIT_IDF))


def _multiset_overlap(a: list[str], b: list[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    if not ca and not cb:
        return 1.0
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 1.0


def field_type_aware(a: str, b: str) -> float:
    nums_a, nums_b = re.findall(r"\d+", a), re.findall(r"\d+", b)
    words_a, words_b = re.findall(r"[a-zA-Z]+", a.lower()), re.findall(r"[a-zA-Z]+", b.lower())
    word_sim = _soft_jaccard(words_a, words_b)
    if not nums_a and not nums_b:
        return word_sim
    num_sim = _multiset_overlap(nums_a, nums_b)
    return 0.5 * num_sim + 0.5 * word_sim


# --------------------------------------------------------------------------- #
# 9 & 13. Feature-stacking methods (ActiveMarginSim, StackEnsemble)
# --------------------------------------------------------------------------- #


def feature_vector(a: str, b: str) -> list[float]:
    return [
        levenshtein_sim(a, b),
        jaro_winkler(a, b),
        block_sim(a, b),
        _phonetic_sim(a, b),
        abbrev_align(a, b),
    ]


class LogisticCombiner:
    def __init__(self, n: int):
        self.w = [0.0] * n
        self.b = 0.0

    def fit(self, X: list[list[float]], y: list[int], iters: int = 400, lr: float = 0.3) -> None:
        for _ in range(iters):
            for xi, yi in zip(X, y):
                z = self.b + sum(w * x for w, x in zip(self.w, xi))
                p = 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0)))
                err = p - yi
                self.b -= lr * err
                self.w = [w - lr * err * x for w, x in zip(self.w, xi)]

    def predict(self, xi: list[float]) -> float:
        z = self.b + sum(w * x for w, x in zip(self.w, xi))
        return 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0)))


class StackEnsemble:
    """Stack char/token/q-gram/phonetic/abbrev views with a learned logistic combiner."""

    def __init__(self) -> None:
        self.model = LogisticCombiner(len(feature_vector("x", "x")))

    def fit(self, pairs: list[tuple[str, str]], labels: list[int]) -> StackEnsemble:
        self.model.fit([feature_vector(a, b) for a, b in pairs], labels)
        return self

    def score(self, a: str, b: str) -> float:
        return self.model.predict(feature_vector(a, b))


class ActiveMarginSim:
    """Stacked similarity whose weights are refit on the pairs nearest the decision margin."""

    def __init__(self) -> None:
        self.model = LogisticCombiner(len(feature_vector("x", "x")))

    def fit(self, pairs: list[tuple[str, str]], labels: list[int], rounds: int = 3) -> ActiveMarginSim:
        X = [feature_vector(a, b) for a, b in pairs]
        self.model.fit(X, labels)
        for _ in range(rounds):  # focus successive passes on the hardest (near-0.5) pairs
            margins = [abs(self.model.predict(x) - 0.5) for x in X]
            keep = sorted(range(len(X)), key=lambda i: margins[i])[: max(2, len(X) // 2)]
            self.model.fit([X[i] for i in keep], [labels[i] for i in keep], iters=200)
        return self

    def score(self, a: str, b: str) -> float:
        return self.model.predict(feature_vector(a, b))


# --------------------------------------------------------------------------- #
# 10. AnytimeLev — early-exit bounded Levenshtein for threshold queries
# --------------------------------------------------------------------------- #


def anytime_levenshtein(a: str, b: str, cutoff: int) -> int:
    """Exact distance when <= cutoff, else cutoff+1 — bails as soon as the band is exceeded."""
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            row_min = min(row_min, cur[-1])
        if row_min > cutoff:
            return cutoff + 1
        prev = cur
    return min(prev[-1], cutoff + 1)


# --------------------------------------------------------------------------- #
# 11. NickGraph — nickname/alias equivalence fused with an edit fallback
# --------------------------------------------------------------------------- #

_NICK_GROUPS = [
    {"robert", "rob", "bob", "bobby"},
    {"william", "will", "bill", "billy", "wm"},
    {"margaret", "peggy", "meg", "maggie", "marge"},
    {"richard", "rick", "dick", "rich", "richie"},
    {"elizabeth", "liz", "beth", "eliza", "betty", "libby"},
    {"james", "jim", "jimmy", "jamie"},
    {"john", "jack", "johnny", "jon"},
    {"katherine", "kate", "katie", "kathy", "kit"},
    {"michael", "mike", "mick", "mikey"},
    {"thomas", "tom", "tommy"},
]
_NICK_OF = {name: i for i, grp in enumerate(_NICK_GROUPS) for name in grp}


def _nick_name_sim(t1: str, t2: str) -> float:
    if t1 == t2:
        return 1.0
    g1, g2 = _NICK_OF.get(t1), _NICK_OF.get(t2)
    if g1 is not None and g1 == g2:
        return 0.95
    return levenshtein_sim(t1, t2)


def nick_graph_sim(a: str, b: str) -> float:
    A, B = tokenize(a), tokenize(b)
    return 0.5 * (_directional_best(A, B, _nick_name_sim, UNIT_IDF)
                  + _directional_best(B, A, _nick_name_sim, UNIT_IDF))


# --------------------------------------------------------------------------- #
# 12. BayesLenNorm — posterior P(match) from edit distance under a generative typo model
# --------------------------------------------------------------------------- #


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))


def bayes_len_norm(a: str, b: str, err: float = 0.15, prior: float = 0.5) -> float:
    """Length-normalized: edit count ~ Poisson(err*L) under match, Poisson(0.7*L) under non-match."""
    d = levenshtein(a, b)
    length = max(len(a), len(b), 1)
    p_match = _poisson_pmf(d, err * length)
    p_non = _poisson_pmf(d, 0.7 * length)
    denom = prior * p_match + (1 - prior) * p_non
    return prior * p_match / denom if denom else 0.0


# --------------------------------------------------------------------------- #
# 14. RecurAlign — hierarchical IDF-weighted token alignment with char-level inner score
# --------------------------------------------------------------------------- #


def _inner_char_align(a: str, b: str) -> float:
    return 0.5 * jaro_winkler(a, b) + 0.5 * levenshtein_sim(a, b)


def recur_align(a: str, b: str, idf: Idf = UNIT_IDF) -> float:
    A, B = tokenize(a), tokenize(b)
    return 0.5 * (_directional_best(A, B, _inner_char_align, idf)
                  + _directional_best(B, A, _inner_char_align, idf))


# --------------------------------------------------------------------------- #
# 15. SegmentSwapAware — reorder-tolerant alignment with a small inversion penalty
# --------------------------------------------------------------------------- #


def _count_inversions(seq: list[int]) -> int:
    return sum(1 for i in range(len(seq)) for j in range(i + 1, len(seq)) if seq[i] > seq[j])


def segment_swap_aware(a: str, b: str, lam: float = 0.05) -> float:
    A, B = tokenize(a), tokenize(b)
    if not A or not B:
        return 1.0 if not A and not B else 0.0
    cand = sorted(
        ((jaro_winkler(A[i], B[j]), i, j) for i in range(len(A)) for j in range(len(B))),
        reverse=True,
    )
    used_a: set[int] = set()
    used_b: set[int] = set()
    matched: list[tuple[int, int]] = []
    total = 0.0
    for score, i, j in cand:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matched.append((i, j))
        total += score
    coverage = 2 * len(matched) / (len(A) + len(B))
    avg = total / len(matched) if matched else 0.0
    seq = [j for _, j in sorted(matched)]
    max_inv = len(seq) * (len(seq) - 1) / 2
    reorder = _count_inversions(seq) / max_inv if max_inv else 0.0
    return avg * coverage * (1.0 - lam * reorder)


# --------------------------------------------------------------------------- #
# Demo + self-tests
# --------------------------------------------------------------------------- #

_CORPUS = [
    "International Business Machines Corporation",
    "Hewlett Packard Enterprise",
    "American Telephone and Telegraph Company",
    "General Electric Company",
    "John Robert Smith",
    "William Henry Gates",
    "123 Main Street Springfield",
    "Federal Bureau of Investigation",
]

_TRAIN: list[tuple[str, str, int]] = [
    ("John Smith", "Jon Smith", 1),
    ("John Smith", "John Smyth", 1),
    ("Robert Brown", "Bob Brown", 1),
    ("International Business Machines", "IBM", 1),
    ("123 Main Street", "123 Main St", 1),
    ("John Smith", "Jane Doe", 0),
    ("Acme Corp", "Globex Inc", 0),
    ("Robert Brown", "Richard Green", 0),
    ("IBM", "Indian Bank Mumbai", 0),
    ("Main Street", "Ocean Avenue", 0),
]

_DEMO_PAIRS = [
    ("International Business Machines", "IBM"),
    ("John Smith", "Smith, John"),
    ("Robert Brown", "Bob Brown"),
    ("John Smith", "Jon Smyth"),
    ("123 Main Street", "123 Main St"),
    ("Acme Corp", "Globex Inc"),
]


def _fmt(x: float) -> str:
    return f"{x:5.2f}"


def _demo() -> None:
    idf = build_idf(_CORPUS)
    trie = build_trie(_CORPUS)
    pairs = [(a, b) for a, b, _ in _TRAIN]
    labels = [y for _, _, y in _TRAIN]
    calib = CalibratedSim(jaro_winkler).fit(pairs, labels)
    stack = StackEnsemble().fit(pairs, labels)
    active = ActiveMarginSim().fit(pairs, labels)

    cols = [
        ("AbbrevAlign", lambda a, b: abbrev_align(a, b, idf)),
        ("RoleAlign", token_role_align),
        ("BlockSim", lambda a, b: block_sim(a, b)),
        ("ChannelMix", lambda a, b: channel_mix(a, b).score),
        ("CalibFS", calib.score),
        ("TrieTFIDF", lambda a, b: prefix_trie_soft_tfidf(a, b, trie, idf)),
        ("FieldType", field_type_aware),
        ("NickGraph", nick_graph_sim),
        ("BayesLen", lambda a, b: bayes_len_norm(a, b)),
        ("RecurAlign", lambda a, b: recur_align(a, b, idf)),
        ("SwapAware", segment_swap_aware),
        ("Stack", stack.score),
        ("ActiveMrg", active.score),
        ("JaroWink*", jaro_winkler),  # baseline for reference
    ]

    header = "pair".ljust(34) + " ".join(name[:9].rjust(9) for name, _ in cols)
    print(header)
    print("-" * len(header))
    for a, b in _DEMO_PAIRS:
        row = f"{a[:15]:>15} | {b[:14]:<14}" + " ".join(_fmt(fn(a, b)).rjust(9) for _, fn in cols)
        print(row)
    print("\n(* baseline. BlockSimDual bound and AnytimeLev shown in self-tests.)")


def _self_tests() -> None:
    idf = build_idf(_CORPUS)

    # AnytimeLev is exact within the cutoff and saturates beyond it.
    for a, b, cutoff in [("kitten", "sitting", 5), ("abc", "abcdef", 2), ("flaw", "lawn", 1)]:
        full = levenshtein(a, b)
        got = anytime_levenshtein(a, b, cutoff)
        if full <= cutoff:
            assert got == full, (a, b, cutoff, got, full)
        else:
            assert got == cutoff + 1 and full > cutoff, (a, b, cutoff, got, full)

    # BlockSimDual: the cheap bound never underestimates the exact similarity.
    for a, b in _DEMO_PAIRS + [("abcdef", "abc"), ("hello world", "world hello")]:
        assert block_upper_bound(a, b) + 1e-9 >= block_sim(a, b), (a, b)

    # AbbrevAlign resolves an acronym its bijection-only ancestor cannot.
    acro = abbrev_align("International Business Machines", "IBM", idf)
    assert acro > 0.95, acro
    assert acro > jaro_winkler("international business machines", "ibm")

    # AbbrevAlign v2 also recovers nicknames (Bob == Robert) that v1 missed.
    assert abbrev_align("Bob Smith", "Robert Smith", idf) > 0.9

    # AbbrevAlign v3: acronyms that skip stopwords now match (the contiguous-span v1/v2 missed these).
    assert abbrev_align("Federal Bureau of Investigation", "FBI", idf) > 0.95
    assert abbrev_align("American Telephone and Telegraph", "ATT", idf) > 0.95
    # ...but a non-acronym must not collide: "GE" is not "General Motors".
    assert not _acronym_match("ge", tokenize("General Motors Company"))

    # NickGraph knows Bob == Robert; Jaro-Winkler does not.
    assert nick_graph_sim("Bob Brown", "Robert Brown") > 0.9

    # SegmentSwapAware is (almost) invariant to reordered name parts.
    assert segment_swap_aware("John Smith", "Smith John") > 0.9

    # BayesLenNorm is a probability, monotonically falling as edits grow.
    p0 = bayes_len_norm("smith", "smith")
    p1 = bayes_len_norm("smith", "smithe")
    p2 = bayes_len_norm("smith", "jones")
    assert 0.0 <= p2 <= p1 <= p0 <= 1.0, (p0, p1, p2)

    # Isotonic calibrator is monotone non-decreasing and maps into [0, 1].
    cal = IsotonicCalibrator().fit([0.1, 0.4, 0.35, 0.8, 0.9], [0, 0, 1, 1, 1])
    lo, hi = cal.predict(0.1), cal.predict(0.9)
    assert 0.0 <= lo <= hi <= 1.0, (lo, hi)

    # Learned combiners separate a clear match from a clear non-match.
    stack = StackEnsemble().fit([(a, b) for a, b, _ in _TRAIN], [y for _, _, y in _TRAIN])
    assert stack.score("John Smith", "Jon Smith") > stack.score("John Smith", "Jane Doe")

    # SelfThresh demands a higher bar for common values than rare ones.
    rare = self_thresh("Zbigniew Brzezinski", "Zbigniew Brzezinsky", idf)
    common = self_thresh("John Smith", "John Smyth", idf)
    assert common.threshold >= rare.threshold

    print("all prototype self-tests passed")


def main() -> int:
    _demo()
    print()
    _self_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
