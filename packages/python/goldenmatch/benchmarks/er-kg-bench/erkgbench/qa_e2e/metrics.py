"""Answer-quality metrics. Pure functions over predictions + gold so each is
unit-testable on tiny fixtures with no LLM. EM/F1 use SQuAD/MuSiQue-style
normalization (lowercase, strip punctuation + articles, collapse whitespace)."""
from __future__ import annotations

import re
import string
from collections import Counter, defaultdict
from collections.abc import Iterable

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)


def _normalize(s: str) -> str:
    s = _canonicalize_spans(s.lower())   # NEW: canonicalize while punctuation/structure intact
    s = s.translate(_PUNCT)
    s = _ARTICLES.sub(" ", s)
    return " ".join(s.split())


def exact_match(pred: str, gold: str) -> float:
    return 1.0 if _normalize(pred) == _normalize(gold) else 0.0


def answer_match(pred: str, gold: str) -> float:
    """Containment correctness for free-text / generative answers: 1.0 if the
    normalized gold answer appears as a contiguous token run inside the normalized
    prediction, else 0.0.

    Generative engines return a sentence ("the final entity is Acme Corp"), so
    whole-string `exact_match` reads ~0 even when the answer is right; containment
    captures "the answer names the correct entity" without an LLM judge. Token-level
    (not raw substring) so a gold "acme" can't spuriously match inside "acmecorp"."""
    g = _normalize(gold).split()
    p = _normalize(pred).split()
    if not g:
        return 1.0 if not p else 0.0
    if len(g) > len(p):
        return 0.0
    for i in range(len(p) - len(g) + 1):
        if p[i : i + len(g)] == g:
            return 1.0
    return 0.0


def token_f1(pred: str, gold: str) -> float:
    p = _normalize(pred).split()
    g = _normalize(gold).split()
    if not p or not g:
        return 1.0 if p == g else 0.0
    overlap = sum((Counter(p) & Counter(g)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(p)
    recall = overlap / len(g)
    return 2 * precision * recall / (precision + recall)


def supporting_fact_recall(
    retrieved_ids: Iterable[str], gold_ids: Iterable[str]
) -> float:
    gold = set(gold_ids)
    if not gold:
        return 1.0
    return len(gold & set(retrieved_ids)) / len(gold)


# --- LLM-judge answer equivalence ---------------------------------------------
#
# `answer_match` (gold-token containment) is FORMAT-SENSITIVE: a verbose essay
# trivially contains the gold span, while a terse one-entity answer is all-or-
# nothing -- so it scores essay-style RAG engines generously and precise-answer
# engines harshly (2026-06-23 head-to-head: ms_graphrag essays vs goldengraph's
# `Answer: X`). The judge asks a fixed model whether the prediction CONVEYS the
# gold answer regardless of verbosity, applied uniformly to every engine, so the
# comparison is fair. Kept ALONGSIDE answer_match (not replacing it). The prompt +
# parse are pure/unit-testable; the call itself is wired in the harness.

_JUDGE_PROMPT = (
    "You are grading a question-answering system. Decide whether the PREDICTION "
    "correctly answers the QUESTION, using the reference GOLD answer as ground "
    "truth. The prediction is CORRECT if it conveys the same answer as the gold -- "
    "even if phrased differently, more verbose, or with extra context. It is "
    "INCORRECT if it gives a different answer, contradicts the gold, only hedges "
    "without committing, or says it cannot answer. Reply with exactly one word: "
    "YES or NO.\n"
    "QUESTION: {question}\nGOLD: {gold}\nPREDICTION: {pred}"
)


def judge_prompt(question: str, gold: str, pred: str) -> str:
    return _JUDGE_PROMPT.format(question=question, gold=gold, pred=pred)


def parse_judge(text: str) -> float:
    """Map an LLM judge verdict to 1.0 (YES) / 0.0 (NO or anything else). The first
    token decides; a leading hedge falls back to a standalone 'yes' search."""
    t = (text or "").strip().lower()
    if t.startswith("yes"):
        return 1.0
    if t.startswith("no"):
        return 0.0
    return 1.0 if "yes" in t.replace("'", " ").replace(".", " ").split() else 0.0


# --- answer-type classification ------------------------------------------------
#
# An entity-graph engine (goldengraph) can ONLY ever answer with a NODE -- a named
# entity. MuSiQue gold answers are frequently NOT entities (a date, a money amount,
# a descriptive phrase), so those questions are unanswerable-by-construction and
# drag the headline `answer_match` down regardless of retrieval/synthesis quality.
# Classifying the gold lets the harness report `answer_match` on the entity-
# answerable subset -- the honest denominator for a graph engine (the 2026-06-23
# N=50 trace: ~60% of losses were non-entity golds like '$72,641', '11 February
# 1929', 'built on 16-bit architectures ...'). Heuristic + approximate by design;
# the subset metric is a framing aid, not a second source of truth.

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october"
    "|november|december"
)

# --- fair-metric span canonicalization ----------------------------------------
#
# Canonicalize equivalent date/time/number spellings to one form BEFORE the
# punctuation/article strip in `_normalize`, so equivalent answers compare equal
# without making distinct answers collide. Reuses `_MONTHS` (above).

_MONTH_NUM = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(),
    start=1,
)}

# Non-anchored date spans (input is already lowercased by `_normalize`). Each ->
# ISO `YYYY-MM-DD`; the later punctuation-strip collapses the dashes so all formats
# converge. A BARE year is deliberately NOT matched here (left as the 4-digit token),
# so it never collides with a full date.
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS})\s+(\d{{3,4}})\b")          # 11 february 1929
_DATE_MDY = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{3,4}})\b")        # february 11, 1929
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")                       # 1929-02-11
_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")                   # 02/11/1929 (M/D/Y)


def _iso(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _canon_dates(s: str) -> str:
    s = _DATE_DMY.sub(lambda m: _iso(int(m.group(3)), _MONTH_NUM[m.group(2)], int(m.group(1))), s)
    s = _DATE_MDY.sub(lambda m: _iso(int(m.group(3)), _MONTH_NUM[m.group(1)], int(m.group(2))), s)
    s = _DATE_ISO.sub(lambda m: _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), s)
    s = _DATE_SLASH.sub(
        lambda m: _iso(int(m.group(3)) + (1900 if int(m.group(3)) < 100 else 0),
                       int(m.group(1)), int(m.group(2))), s)
    return s


# 5am / 5 am / 5 a.m. / 5 AM -> "5am"; 5pm / 5 p.m. -> "5pm". hour (+ optional :minute) only.
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\b")


def _canon_times(s: str) -> str:
    def repl(m):
        hh, mm, ap = m.group(1), m.group(2), m.group(3)
        return f"{int(hh)}{(':' + mm) if mm else ''}{ap}m"
    return _TIME_RE.sub(repl, s)


def _canonicalize_spans(s: str) -> str:
    """Canonicalize date/time/standalone-number-word spans in a LOWERCASED string so equivalent
    answers compare equal after `_normalize`. Narrow + fail-soft: only the recognized span types are
    touched; everything else (and anything out of scope) passes through unchanged."""
    return _canon_times(_canon_dates(s))


_DATE_RE = re.compile(
    rf"^\s*(\d{{1,2}}\s+)?({_MONTHS})\s+\d{{3,4}}\s*$"  # 11 February 1929 / March 1929
    rf"|^\s*({_MONTHS})\s+\d{{1,2}}\s*,?\s*\d{{3,4}}\s*$"  # February 11, 1929
    rf"|^\s*(19|20)\d{{2}}\s*$"  # bare 4-digit year
    rf"|^\s*\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}\s*$",  # 02/11/1929
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"^[\$£€]?\s*\d[\d,\.]*\s*"
    r"(%|am|pm|st|nd|rd|th|km|kg|mi|ft|m|million|billion|thousand|hundred|"
    r"percent|dollars?|years?)?\s*$",
    re.IGNORECASE,
)
_ARTICLES_LEADING = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_STOPWORDS = frozenset(
    "the a an of in on at to and or for with by from as".split()
)


def classify_answer_type(gold: str) -> str:
    """Coarse type of a gold answer: 'entity' | 'date' | 'number' | 'phrase'.

    'entity' = a short, proper-noun-dominated name an entity-graph could emit
    ('Exeter College', 'the Politburo', 'Sega Genesis'). 'date'/'number' =
    literals the graph cannot emit. 'phrase' = a descriptive clause
    ('built on 16-bit architectures ...'). Heuristic, not authoritative."""
    g = (gold or "").strip()
    if not g:
        return "phrase"
    if _DATE_RE.search(g):
        return "date"
    if _NUMBER_RE.match(g):
        return "number"
    core = _ARTICLES_LEADING.sub("", g)
    toks = re.findall(r"[A-Za-z0-9.&'-]+", core)
    if not toks:
        return "number" if any(c.isdigit() for c in g) else "phrase"
    alpha = [t for t in toks if t[:1].isalpha() and t.lower() not in _STOPWORDS]
    if not alpha:
        return "phrase"
    capitalized = sum(1 for t in alpha if t[:1].isupper())
    # A name is short and mostly Title-Case; a descriptive phrase is longer and
    # mostly lowercase.
    if len(toks) <= 6 and capitalized / len(alpha) >= 0.6:
        return "entity"
    return "phrase"


def is_entity_answer(gold: str) -> bool:
    """True when the gold answer is a named entity an entity-graph could emit."""
    return classify_answer_type(gold) == "entity"


def decay_curve(rows: Iterable[tuple[int, float]]) -> dict[int, float]:
    """rows: (hop_count, correct in {0.0,1.0}) -> {hop_count: mean correctness}."""
    by_hop: dict[int, list[float]] = defaultdict(list)
    for hop, correct in rows:
        by_hop[hop].append(correct)
    return {hop: sum(v) / len(v) for hop, v in sorted(by_hop.items())}
