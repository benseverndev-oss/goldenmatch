"""Scorer for GoldenMatch — field-level and pair-level scoring."""

from __future__ import annotations

import datetime as _dt
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from rapidfuzz.distance import DamerauLevenshtein, JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_sort_ratio
from rapidfuzz.process import cdist

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core._profile_helpers import (
    hartigan_dip,
    histogram_20,
    mass_above,
    mass_borderline,
)
from goldenmatch.core.complexity_profile import ScoringProfile
from goldenmatch.core.profile_emitter import current_emitter, has_active_emitter
from goldenmatch.utils.transforms import apply_transforms, canonical_soundex

logger = logging.getLogger(__name__)

# Process-level cache of NE (scorer, field) entries that already raised once.
# `_apply_negative_evidence` is called per-pair inside a hot loop; without this
# guard a broken NE entry (e.g. an unregistered scorer name like 'ensemble' picked
# by auto-config) emits a WARNING per pair, hammering downstream log sinks. At
# Railway-scale (10M+ rows, bucket backend) this was producing 140K msgs/sec —
# enough to trip Railway's 500 logs/sec replica limit and stall the container.
# Set holds a tuple of (scorer_name, field_name); first failure logs + records,
# subsequent failures for the same key are silent and short-circuit before
# re-invoking the failing scorer at all.
_NE_BROKEN: set[tuple[str, str]] = set()
# `_NE_BROKEN` is mutated from the ThreadPoolExecutor block-scoring workers
# (`score_blocks_parallel` / `_columnar`). The GIL keeps the set itself
# consistent, but it is a PROCESS global: without a reset, a transient or
# config-specific NE failure in one dedupe would stay "broken" for every later
# dedupe in the same process -- a real leak in a long-lived MCP/A2A server. The
# scoring entry points call `reset_ne_broken()` (single-threaded, before
# fan-out) so each run re-evaluates NE freshly. The lock guards the writes (and
# matters on free-threaded/no-GIL builds where `set` ops are no longer atomic).
_NE_BROKEN_LOCK = threading.Lock()


def reset_ne_broken() -> None:
    """Clear the per-process record of NE ``(scorer, field)`` entries that
    failed once. Called at the start of each scoring run so a broken NE entry
    from a prior dedupe doesn't silently skip a valid one in the next."""
    with _NE_BROKEN_LOCK:
        _NE_BROKEN.clear()


def _emit_scoring_profile(
    pairs: list[tuple[int, int, float]],
    threshold: float,
    *,
    candidates_compared: int = 0,
    per_field_variance: dict[str, float] | None = None,
) -> None:
    """Emit ScoringProfile to current emitter. No-op when emitter is null.

    Args:
        pairs: Pairs *above* the threshold (returned by find_fuzzy_matches).
        threshold: Score threshold used to filter pairs.
        candidates_compared: Total candidate pairs evaluated before threshold
            filtering.  Distinct from ``len(pairs)`` which only counts matches.
            Approximation: sum of n*(n-1)//2 for each block processed.
        per_field_variance: Optional per-field score variance dict.
    """
    # Build the profile only when a capture is open to consume it. On the full
    # production pass there is no ``profile_capture`` (all captures live in the
    # auto-config controller's sample iterations), so ``current_emitter()`` is
    # the null singleton and ``set_scoring`` is a no-op -- but histogram_20 +
    # hartigan_dip + the mass_* passes below run over EVERY scored pair (~131M
    # at 1M rows: the dip is a numpy/diptest sort, the rest are full-list Python
    # loops). That ~149s of work was computed and immediately discarded. Same
    # dead-work pattern as #837's matched_pairs; the guard makes the no-op path
    # actually do no work. When a capture IS active (sample iterations) the
    # construction is byte-identical to before.
    if not has_active_emitter():
        return
    scores = [s for _, _, s in pairs]
    profile = ScoringProfile(
        n_pairs_scored=len(scores),
        candidates_compared=candidates_compared,
        score_histogram=histogram_20(scores),
        dip_statistic=hartigan_dip(scores),
        mass_above_threshold=mass_above(scores, threshold),
        mass_in_borderline=mass_borderline(scores, threshold),
        per_field_score_variance=per_field_variance or {},
    )
    current_emitter().set_scoring(profile)


def _initialism_match_single(val_a: str, val_b: str) -> float:
    """1.0 if either string is the other's initialism, or their initialisms
    are equal; else 0.0.

    "IBM" <-> "International Business Machines" matches because
    ``derive_initialism("International Business Machines") == "IBM" == val_a``.
    Empty-guarded: a derived ``""`` (single-token / empty input) never
    matches, so two distinct single-token names ("Apple"/"Apricot") score 0.0.
    Symmetric. The initialism collision ("IBM" <-> "Indian Banana Market") is
    a known false positive — it scores 1.0 by design.
    """
    from goldenmatch.core.acronym import derive_initialism

    ia = derive_initialism(val_a) or ""
    ib = derive_initialism(val_b) or ""
    if (
        (ia and ia == val_b)
        or (ib and val_a == ib)
        or (ia and ib and ia == ib)
    ):
        return 1.0
    return 0.0


def _alias_match_single(val_a: str, val_b: str) -> float:
    """1.0 if both values canonicalize to the same NON-EMPTY business alias
    OR the same given-name canonical; else 0.0.

    "Acme Inc" <-> "Acme Incorporated" both map to the business canonical
    "acme"; "Bob" <-> "Robert" both map to the given-name canonical "robert".
    Empty-guarded: an empty canonical never matches (both ``""`` -> 0.0).
    Note canonicalization is an idempotent passthrough for OOV names, so two
    *different* OOV names ("Acme"/"Globex") yield different canonicals -> 0.0.
    """
    from goldenmatch.refdata.business_aliases import canonical_company_form
    from goldenmatch.refdata.given_names import canonical_form

    cb_a = canonical_company_form(val_a) or ""
    cb_b = canonical_company_form(val_b) or ""
    if cb_a and cb_a == cb_b:
        return 1.0
    cg_a = canonical_form(val_a) or ""
    cg_b = canonical_form(val_b) or ""
    if cg_a and cg_a == cg_b:
        return 1.0
    return 0.0


def _iso_date_digits(s: str) -> str | None:
    """The 8 packed digits of an ISO-8601 ``YYYY-MM-DD`` string, or None if it
    isn't that exact shape. Pure-Python mirror of score-core's ``iso_date_digits``
    (strict: no locale parsing, month/day ranges not validated)."""
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return None
    digits = s[:4] + s[5:7] + s[8:10]
    return digits if digits.isdigit() else None


def _date_similarity_py(val_a: str, val_b: str) -> float:
    """Pure-Python fallback mirror of score-core ``date_similarity`` (id 4).

    Byte-identical to the Rust reference (asserted in the native-parity suite):
    Damerau-Levenshtein over the 8 canonical ISO digits, mapped so a single-digit
    typo stays above a typical 0.85 cutoff and an unrelated date cliffs to 0;
    plain ``levenshtein`` fallback when either side isn't an ISO date. See #1858
    (``jaro_winkler`` scores unrelated birthdays 0.80+)."""
    da, db = _iso_date_digits(val_a), _iso_date_digits(val_b)
    if da is not None and db is not None:
        d = DamerauLevenshtein.distance(da, db)
        return {0: 1.0, 1: 0.90, 2: 0.75}.get(d, 0.0)
    return Levenshtein.normalized_similarity(val_a, val_b)


# --- date_diff: magnitude-aware date comparator ----------------------------
# Spec: docs/superpowers/specs/2026-07-23-fs-domain-comparators-design.md
# The edit-distance `date` scorer above is magnitude-BLIND: 1990-01-02 vs
# 1991-01-02 differs by one digit -> 0.90, over-scoring a full-year DOB gap.
# `date_diff` parses both to a day-ordinal and bands by DAY-DISTANCE, so a year
# gap is a weak partial. It is a strict improvement: on unparseable input it
# reuses `_date_similarity_py` (never returns None for non-null strings), so the
# scalar and vectorized (NxN loop) paths call the SAME function and agree by
# construction, and the missing-level decision is unchanged.
# Day-distance -> similarity, monotone non-increasing. Person-data defaults;
# a config's `level_thresholds` re-bucket the raw value if a caller wants other
# cutoffs. 1827 d ~= 5 y.
_DATE_DIFF_BANDS: tuple[tuple[int, float], ...] = (
    (0, 1.0), (1, 0.92), (31, 0.80), (366, 0.60), (1827, 0.30),
)


def _date_parts(s: str | None) -> tuple[int, int, int] | None:
    """Parse a date to ``(year, month, day)`` -- tolerant of ISO ``YYYY-MM-DD``
    (and ``/`` separators, 1-digit month/day), compact ``YYYYMMDD``, and a bare
    ``YYYY`` (year-only, e.g. NCVR birth_year -> Jan-1 of that year). None if it
    matches none of these. Range validity (e.g. month 13) is checked at ordinal
    time, not here. Regex-free (``re`` isn't imported in this module)."""
    if not s:
        return None
    s = s.strip()
    sep = "-" if "-" in s else ("/" if "/" in s else "")
    if sep:
        bits = s.split(sep)
        if len(bits) == 3 and all(b.isdigit() for b in bits) and len(bits[0]) == 4:
            return int(bits[0]), int(bits[1]), int(bits[2])
        return None
    if len(s) == 8 and s.isdigit():
        return int(s[:4]), int(s[4:6]), int(s[6:8])
    if len(s) == 4 and s.isdigit():
        return int(s), 1, 1
    return None


def _date_ordinal_of(parts: tuple[int, int, int] | None) -> int | None:
    if parts is None:
        return None
    try:
        return _dt.date(parts[0], parts[1], parts[2]).toordinal()
    except ValueError:
        return None  # invalid month/day (e.g. 1990-13-40)


def _parse_date_ordinal(s: str | None) -> int | None:
    """Proleptic-Gregorian day ordinal for a parseable date, else None."""
    return _date_ordinal_of(_date_parts(s))


def _date_diff_band(d: int) -> float:
    for lim, val in _DATE_DIFF_BANDS:
        if d <= lim:
            return val
    return 0.0


def _date_diff_similarity_py(val_a: str, val_b: str) -> float:
    """Day-distance banded similarity; MM/DD transposition floored to a partial;
    edit-distance (`_date_similarity_py`) fallback when either side won't parse."""
    oa, ob = _parse_date_ordinal(val_a), _parse_date_ordinal(val_b)
    if oa is None or ob is None:
        return _date_similarity_py(val_a, val_b)
    d = abs(oa - ob)
    if d != 0:
        # A month<->day swap on either operand that collapses the distance to 0
        # is a data-entry transposition (1990-01-02 vs 1990-02-01), a partial not
        # a disagree -> floor at the <=31-day band. (Re-parse parts here only --
        # both operands parsed above, so this is never None on the covered path.)
        for parts, other in ((_date_parts(val_a), ob), (_date_parts(val_b), oa)):
            if parts is None:
                continue
            y, mo, dd = parts
            sw = _date_ordinal_of((y, dd, mo))
            if sw is not None and abs(sw - other) == 0:
                d = min(d, 31)
                break
    return _date_diff_band(d)


# --- numeric_diff: magnitude-aware numeric comparator ----------------------
# Spec: docs/superpowers/specs/2026-07-23-fs-domain-comparators-design.md
# String similarity on numbers is meaningless -- `levenshtein("100","900")` ~0.67
# reads two very different amounts as near-agreement. `numeric_diff` parses both
# to float and maps the DISTANCE to a monotone [0,1] ramp, in one of two modes:
#   numeric_diff:abs:<eps>  -> distance = |a-b|,                       band = eps
#   numeric_diff:pct:<frac> -> distance = |a-b| / max(|a|,|b|,_PCT_EPS), band = frac
# Similarity = 1.0 at distance 0, linear decay to 0.0 at the band edge, 0 beyond
# (monotone non-increasing). Unparseable input falls back to exact (1.0 if the raw
# strings are equal else 0.0) -- NEVER None -- so the scalar and vectorized (NxN
# loop) paths call the SAME function and agree by construction, and the missing-
# level decision is unchanged (mirrors date_diff's fallback posture).
_NUMERIC_DIFF_DEFAULT: tuple[str, float] = ("pct", 0.1)  # bare form -> 10% band
_PCT_EPS = 1e-9  # guards the pct denominator when both values are ~0


def _parse_numeric_diff_spec(scorer: str) -> tuple[str, float]:
    """``numeric_diff[:abs|pct:<band>]`` -> ``(mode, band)``; bare / malformed
    suffix -> the default (``pct``, 0.1)."""
    parts = scorer.split(":")
    if len(parts) == 3 and parts[0] == "numeric_diff" and parts[1] in ("abs", "pct"):
        try:
            band = float(parts[2])
        except ValueError:
            return _NUMERIC_DIFF_DEFAULT
        if band > 0.0:
            return parts[1], band
    return _NUMERIC_DIFF_DEFAULT


def _parse_float(s: str | None) -> float | None:
    """Float value of a string, else None (also None for the special float
    literals ``nan``/``inf`` -- a garbage numeric must read as unparseable, not a
    distance)."""
    if s is None:
        return None
    try:
        v = float(s.strip())
    except (ValueError, AttributeError):
        return None
    return v if math.isfinite(v) else None


def _numeric_diff_similarity_py(val_a: str, val_b: str, scorer: str) -> float:
    """Banded numeric-distance similarity; exact-string fallback when either side
    won't parse as a finite float (so it never returns None for non-null input)."""
    a, b = _parse_float(val_a), _parse_float(val_b)
    if a is None or b is None:
        return 1.0 if val_a == val_b else 0.0
    mode, band = _parse_numeric_diff_spec(scorer)
    dist = abs(a - b)
    if mode == "pct":
        dist = dist / max(abs(a), abs(b), _PCT_EPS)
    if dist >= band:
        return 0.0
    return 1.0 - dist / band  # 1.0 at dist 0, linear decay to the band edge


# --- geo_haversine: great-circle distance comparator -----------------------
# Spec: docs/superpowers/specs/2026-07-23-fs-domain-comparators-design.md
# String similarity on coordinates is meaningless; great-circle distance is the
# signal. `geo_haversine` parses ONE combined "lat,long" field per side and bands
# the haversine distance in km, monotone non-increasing. (Two SEPARATE lat/long
# columns are the deferred CROSS-field comparator -- the single-field
# score_field(a,b,scorer) signature can't reach two columns.) Unparseable input
# falls back to exact (never None), like numeric_diff / date_diff.
# km -> similarity, monotone non-increasing. Address/venue-scale defaults.
_GEO_HAVERSINE_BANDS: tuple[tuple[float, float], ...] = (
    (0.1, 1.0), (1.0, 0.85), (10.0, 0.5), (100.0, 0.2),
)
_EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius


def _parse_latlong(s: str | None) -> tuple[float, float] | None:
    """Parse ``"lat,long"`` (``,`` or ``;`` separator) to ``(lat, long)`` within
    valid coordinate ranges, else None. Regex-free."""
    if not s:
        return None
    txt = s.strip()
    sep = "," if "," in txt else (";" if ";" in txt else "")
    if not sep:
        return None
    bits = txt.split(sep)
    if len(bits) != 2:
        return None
    try:
        lat, lon = float(bits[0].strip()), float(bits[1].strip())
    except ValueError:
        return None
    if math.isfinite(lat) and math.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2.0 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _geo_haversine_band(km: float) -> float:
    for lim, val in _GEO_HAVERSINE_BANDS:
        if km <= lim:
            return val
    return 0.0


def _geo_haversine_similarity_py(val_a: str, val_b: str) -> float:
    """Haversine-distance banded similarity on a ``lat,long`` field; exact-string
    fallback when either side won't parse (never None)."""
    pa, pb = _parse_latlong(val_a), _parse_latlong(val_b)
    if pa is None or pb is None:
        return 1.0 if val_a == val_b else 0.0
    return _geo_haversine_band(_haversine_km(pa, pb))


def score_field(val_a: str | None, val_b: str | None, scorer: str) -> float | None:
    """Score two field values using the specified scorer.

    Returns None if either value is None.
    """
    if val_a is None or val_b is None:
        return None

    if scorer == "exact":
        return 1.0 if val_a == val_b else 0.0
    elif scorer == "date":
        return _date_similarity_py(val_a, val_b)
    elif scorer == "date_diff":
        return _date_diff_similarity_py(val_a, val_b)
    elif scorer == "geo_haversine":
        return _geo_haversine_similarity_py(val_a, val_b)
    elif scorer == "numeric_diff" or scorer.startswith("numeric_diff:"):
        return _numeric_diff_similarity_py(val_a, val_b, scorer)
    elif scorer == "jaro_winkler":
        return JaroWinkler.similarity(val_a, val_b)
    elif scorer == "levenshtein":
        return Levenshtein.normalized_similarity(val_a, val_b)
    elif scorer == "token_sort":
        return token_sort_ratio(val_a, val_b) / 100.0
    elif scorer == "soundex_match":
        return _soundex_score_single(val_a, val_b)
    elif scorer == "ensemble":
        # Scalar twin of the NxN `ensemble` branch in `_fuzzy_score_matrix`:
        # element-wise max of jaro_winkler, token_sort, and soundex*0.8. The
        # vectorized scoring path already handles `ensemble`, but EM training
        # routes through this scalar path (comparison_vector ->
        # _build_comparison_matrix -> score_field). Without this case, a
        # probabilistic matchkey whose auto-config assigns `ensemble` (any
        # `name` field — autoconfig.py build_probabilistic_matchkeys) raises
        # `Unknown scorer` at train time and the Fellegi-Sunter path can't run
        # at all. Soundex is wrapped defensively (jellyfish.soundex can raise on
        # non-alpha input) so that component just drops instead of failing the
        # whole pair.
        return _ensemble_score_single(val_a, val_b)
    elif scorer == "initialism_match":
        return _initialism_match_single(val_a, val_b)
    elif scorer == "alias_match":
        return _alias_match_single(val_a, val_b)
    elif scorer == "dice":
        return _dice_score_single(val_a, val_b)
    elif scorer == "jaccard":
        return _jaccard_score_single(val_a, val_b)
    elif scorer == "qgram":
        return _qgram_score_single(val_a, val_b)
    elif scorer == "phash":
        return _phash_score_single(val_a, val_b)
    elif scorer == "audio_fp":
        return _audio_fp_score_single(val_a, val_b)
    elif scorer == "radial":
        return _radial_score_single(val_a, val_b)
    else:
        # Check plugin registry
        from goldenmatch.plugins.registry import PluginRegistry
        plugin = PluginRegistry.instance().get_scorer(scorer)
        if plugin is not None:
            return plugin.score_pair(val_a, val_b)  # pyright: ignore[reportAttributeAccessIssue]  # plugin protocol is runtime-duck-typed
        raise ValueError(f"Unknown scorer: {scorer!r}")


def score_pair(row_a: dict, row_b: dict, fields: list[MatchkeyField]) -> float:
    """Score a pair of rows across all fields using weighted aggregation.

    Fields that produce None scores are excluded from the average.
    If all fields are None, returns 0.0.
    """
    weighted_sum = 0.0
    weight_sum = 0.0

    for f in fields:
        # score_pair expects fully-populated MatchkeyFields; the MatchkeyConfig
        # validator on weighted matchkeys guarantees field/scorer/weight are
        # non-None at call time. Typed accessors narrow the Optional fields.
        val_a = apply_transforms(row_a.get(f.resolved_field), f.transforms)
        val_b = apply_transforms(row_b.get(f.resolved_field), f.transforms)
        field_score = score_field(val_a, val_b, f.fuzzy_scorer)

        if field_score is not None:
            weighted_sum += field_score * f.fuzzy_weight
            weight_sum += f.fuzzy_weight

    if weight_sum == 0.0:
        return 0.0

    return weighted_sum / weight_sum


def _apply_negative_evidence(matchkey: MatchkeyConfig, pair: dict) -> float:
    """v1.11: compute the total negative-evidence penalty for a pair.

    Returns the sum of penalties for NE fields whose similarity is below
    their threshold. Defensive: skips NE entries with unknown scorers,
    missing fields, or scorer-call exceptions; logs WARNING and continues.

    ``pair`` maps field names to 2-tuples ``(val_a, val_b)`` — the same
    shape used by the scoring loop when passing raw row values. Fields not
    present in ``pair`` are silently skipped.

    Caller is responsible for: ``final_score = max(0.0, score_positive - penalty)``.

    Only applies to weighted matchkeys. Returns 0.0 immediately when
    ``matchkey.negative_evidence`` is None or empty.
    """
    if not matchkey.negative_evidence:
        return 0.0

    total_penalty = 0.0
    for ne in matchkey.negative_evidence:
        if ne.field not in pair:
            continue
        ne_key = (ne.scorer, ne.field)
        if ne_key in _NE_BROKEN:
            # Already known-broken this process; skip without re-invoking the
            # failing scorer (the exception itself is expensive in a hot loop).
            continue
        try:
            val_a, val_b = pair[ne.field]
            val_a = apply_transforms(val_a, ne.transforms)
            val_b = apply_transforms(val_b, ne.transforms)
            sim = score_field(val_a, val_b, ne.scorer)
        except (ValueError, KeyError) as exc:
            with _NE_BROKEN_LOCK:
                _NE_BROKEN.add(ne_key)
            logger.warning(
                "auto-config: NE scorer '%s' for field '%s' not registered or failed: %s; "
                "skipping (further pairs with this NE entry will be silently skipped)",
                ne.scorer, ne.field, exc,
            )
            continue
        except Exception as exc:
            with _NE_BROKEN_LOCK:
                _NE_BROKEN.add(ne_key)
            logger.warning(
                "auto-config: NE scoring of field '%s' raised %s; "
                "skipping (further pairs with this NE entry will be silently skipped)",
                ne.field, type(exc).__name__,
            )
            continue
        if sim is None:
            # One or both values are None — can't score, skip
            continue
        if sim < ne.threshold:
            total_penalty += ne.flat_penalty
    return total_penalty


def _apply_negative_evidence_to_exact_pairs(  # pyright: ignore[reportUnusedFunction]  # called from core/pipeline.py (outside slice)
    pairs: list[tuple[int, int, float]],
    matchkey: MatchkeyConfig,
    full_df: pl.DataFrame,
) -> list[tuple[int, int, float]]:
    """v1.12 Path Y: filter pairs from find_exact_matches by NE penalty.

    ``pairs`` is the output of find_exact_matches: list of (row_id_a, row_id_b, 1.0)
    where each pair already shares the matchkey value. v1.12: subtract NE
    penalties; emit only if final_score >= threshold.

    When matchkey.negative_evidence is None or empty: returns pairs unchanged
    (today's binary behavior preserved).
    """
    if not matchkey.negative_evidence:
        return pairs
    threshold = matchkey.threshold if matchkey.threshold is not None else 0.5
    if matchkey.threshold is None:
        logger.info(
            "auto-config: NE active on exact matchkey '%s' but threshold is None; "
            "using default 0.5 (recommend setting matchkey.threshold explicitly)",
            matchkey.name,
        )

    # Build a lookup of (row_id → row_index_in_full_df) for fast NE column access
    row_id_to_idx: dict[int, int] = dict(
        zip(full_df["__row_id__"].to_list(), range(full_df.height))
    )

    filtered: list[tuple[int, int, float]] = []
    for row_a, row_b, _initial_score in pairs:
        idx_a = row_id_to_idx.get(row_a)
        idx_b = row_id_to_idx.get(row_b)
        if idx_a is None or idx_b is None:
            # Defensive: shouldn't happen if pairs came from find_exact_matches
            continue
        pair_dict: dict = {}
        for ne in matchkey.negative_evidence:
            if ne.field not in full_df.columns:
                continue
            try:
                val_a = full_df[ne.field][idx_a]
                val_b = full_df[ne.field][idx_b]
                pair_dict[ne.field] = (val_a, val_b)
            except Exception:
                pair_dict[ne.field] = (None, None)
        penalty = _apply_negative_evidence(matchkey, pair_dict)
        final_score = max(0.0, 1.0 - penalty)
        if final_score >= threshold:
            filtered.append((row_a, row_b, final_score))
    return filtered


def find_exact_matches(
    lf: Any, mk: MatchkeyConfig
) -> list[tuple[int, int, float]]:
    """Find exact matches by grouping on the matchkey column.

    Uses a Polars self-join on the matchkey column to find all pairs of
    __row_id__ that share the same matchkey value, each with score 1.0.
    Null matchkey values are excluded.
    """
    ids_a, ids_b = _find_exact_match_ids(lf, mk)
    if ids_a.size == 0:
        return []
    return [(int(a), int(b), 1.0) for a, b in zip(ids_a, ids_b)]


def _find_exact_match_ids(
    lf: Any, mk: MatchkeyConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Same Polars self-join as ``find_exact_matches`` but returns the two
    row-id columns as zero-copy int64 numpy arrays -- skipping the
    ``list[tuple[int, int, 1.0]]`` materialization that dominates RSS at
    scale (~3-4 GB of CPython tuple overhead at 36.5M exact pairs).

    Used by the hot caller in ``pipeline.py`` Step exact_matching when the
    matchkey has no negative_evidence + the run isn't across_files_only.
    The legacy ``find_exact_matches`` delegates here for any caller that
    still needs the list[tuple] shape (8 call sites at the time of this
    refactor: chunked, incremental, tui/engine, tests, benchmark scripts)."""
    # W2c: post-collect work routes through the Frame seam (byte-identical
    # PolarsFrame delegation on the default backend; dual-backend once W2d
    # threads Frames past the ingest boundary). The lazy projection stays
    # raw Polars until W2d.
    from goldenmatch.core.frame import to_frame

    mk_col = f"__mk_{mk.name}__"
    # D2s-a (spine descent): dual-rep entry. Legacy pl.LazyFrame callers keep
    # the lazy projection+collect verbatim; a seam Frame (or eager native)
    # projects via the seam so the arrow lane never round-trips polars.
    from goldenmatch.core.frame import is_polars_lazyframe

    if is_polars_lazyframe(lf):
        df = lf.select("__row_id__", mk_col).collect()
    else:
        df = to_frame(lf).select(["__row_id__", mk_col]).native
    # Exclude null AND empty/blank matchkey values (filter_nonblank_key). Two
    # records both missing a field (e.g. a blanked phone -> "") must NOT be an
    # exact match: otherwise every blank-valued record joins on "" and
    # Union-Find transitively explodes the clusters (the DQbench T3 precision
    # collapse, 2026-06-06). Blank != a shared identity claim.
    frame = to_frame(df).filter_nonblank_key(mk_col)
    if frame.height < 2:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    joined = frame.self_join_on(mk_col, "__row_id__")
    if joined.height == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    # to_numpy() on Int64 columns is zero-copy from Arrow. No Python ints
    # are created; the buffer is the same one the engine allocated for the join.
    ids_a = joined.column("__row_id__").to_numpy()
    ids_b = joined.column("__row_id___right").to_numpy()
    return ids_a, ids_b


# ---------------------------------------------------------------------------
# Vectorized helpers for find_fuzzy_matches
# ---------------------------------------------------------------------------

def _get_transformed_values(block_df: pl.DataFrame, field: MatchkeyField) -> list:
    """Get transformed values for a field as a list.

    Fast path: read the precomputed __xform_<sig>__ column populated by
    precompute_matchkey_transforms (called once per pipeline run, eagerly,
    before blocking). Avoids one `.select()` per (block × matchkey field).

    Fallback path: legacy per-block .select(_try_native_chain(...)) for
    callers that bypass the pipeline (DataFrame entry points, tests calling
    find_fuzzy_matches directly).
    """
    from goldenmatch.core.frame import to_frame
    from goldenmatch.core.matchkey import _xform_sig

    frame = to_frame(block_df)
    sig = _xform_sig(field)
    if sig in frame.columns:
        return frame.column(sig).to_list()

    col = field.field
    assert col is not None, "field.field must be set; upstream validation enforces"
    # D5c: derive_transformed_column IS the seam twin of the old
    # _try_native_chain select (native chain when portable, RAW-value python
    # fallback otherwise -- same branches, both backends).
    if field.transforms:
        return frame.derive_transformed_column(col, list(field.transforms)).to_list()

    values = frame.column(col).to_list()
    return [apply_transforms(v, field.transforms) if v is not None else None for v in values]


# Native field-matrix kernel scorer IDs. Mirror score.rs::score_field_matrix
# dispatch. Bloom-filter dice/jaccard (the slow path's _dice_score_matrix /
# _jaccard_score_matrix) are intentionally NOT routed here -- the Rust IDs
# 5/6 are char-bigram, not bloom-filter, semantics. PPRL workloads stay on
# the existing vectorized numpy path.
_NATIVE_FIELD_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0,
    "levenshtein": 1,
    "token_sort": 2,
    "exact": 3,
    "soundex_match": 4,
}


def _native_field_matrix(values: list, scorer_name: str) -> np.ndarray | None:
    """Native cdist-shaped fallback. Returns None when the kernel isn't
    loaded or the scorer isn't supported -- caller stays on the rapidfuzz
    path.

    Self-cdist: passes the same Arrow array on both sides and marks
    `symmetric=True` so the Rust kernel skips half the work.
    """
    scorer_id = _NATIVE_FIELD_SCORER_IDS.get(scorer_name)
    if scorer_id is None:
        return None
    # Reversible gate (single-kernel-collapse R2): field scoring honors the same
    # GOLDENMATCH_NATIVE flag every other native component reads -- `0` forces the
    # pure rapidfuzz path, `1` requires native (raises if absent -- the CI parity
    # contract, intentionally NOT swallowed here), `auto`/unset uses native iff
    # importable AND signed off (`field_scoring` in _GATED_ON). Output is identical
    # either way (pure==kernel is byte/4dp-parity, asserted in
    # tests/test_native_field_matrix_parity.py); the gate only governs WHICH path
    # runs, and makes the long-standing kernel default reversible + telemetered.
    from goldenmatch.core._native_loader import native_enabled, native_module

    if not native_enabled("field_scoring"):
        return None
    native = native_module()
    if native is None or not hasattr(native, "score_field_matrix"):
        return None
    try:
        import pyarrow as pa
        clean = [v if v is not None else "" for v in values]
        arr = pa.array(clean, type=pa.large_string())
        return native.score_field_matrix(arr, arr, scorer_id, True)
    except Exception:
        # Any FFI / pyarrow / numpy hiccup falls through to rapidfuzz.
        return None


def _exact_score_matrix(values: list) -> np.ndarray:
    """NxN exact match matrix using hash-based grouping."""
    n = len(values)
    scores = np.zeros((n, n))
    # Group indices by value (O(n) hash map)
    groups: dict[str, list[int]] = {}
    for i, v in enumerate(values):
        if v is not None:
            groups.setdefault(v, []).append(i)
    # For each group, set all pairs to 1.0
    for indices in groups.values():
        if len(indices) > 1:
            idx = np.array(indices)
            scores[np.ix_(idx, idx)] = 1.0
    return scores


def _fuzzy_score_matrix(
    values: list, scorer_name: str, model_name: str = "all-MiniLM-L6-v2",
    tf_freqs: dict[str, float] | None = None,
) -> np.ndarray:
    """NxN fuzzy score matrix using rapidfuzz cdist or embedding cosine similarity."""
    if scorer_name == "embedding":
        try:
            from goldenmatch.core.embedder import get_embedder

            embedder = get_embedder(model_name)
            embeddings = embedder.embed_column(values, cache_key=f"_block_{id(values)}")
            sim = embedder.cosine_similarity_matrix(embeddings)
            return np.asarray(sim, dtype=np.float64)
        except Exception:
            logger.warning("Embedding scorer failed, falling back to token_sort", exc_info=True)
            scorer_name = "token_sort"

    # Replace None with empty string for cdist (we handle nulls separately)
    clean = [v if v is not None else "" for v in values]

    # All NxN return matrices use float32 — see find_fuzzy_matches' Phase 1
    # comment for the memory math. Ensemble's intermediate matrices in
    # particular allocated 4× NxN float64 = 800 MB at N=5000, which was the
    # largest single contributor to the 1M-row OOM cliff (PR #173).
    if scorer_name == "ensemble":
        # Combine multiple scorers, take element-wise max. Each subscorer
        # tries the native kernel first; falls back to rapidfuzz cdist.
        jw = _native_field_matrix(values, "jaro_winkler")
        if jw is None:
            jw = np.asarray(cdist(clean, clean, scorer=JaroWinkler.similarity), dtype=np.float32)
        ts = _native_field_matrix(values, "token_sort")
        if ts is None:
            ts = np.asarray(cdist(clean, clean, scorer=token_sort_ratio), dtype=np.float32) / 100.0
        sx = _soundex_score_matrix(values).astype(np.float32) * 0.8
        matrix = np.maximum(np.maximum(jw, ts), sx)
    elif scorer_name in ("jaro_winkler", "levenshtein", "token_sort"):
        m = _native_field_matrix(values, scorer_name)
        if m is not None:
            matrix = m
        elif scorer_name == "jaro_winkler":
            matrix = np.asarray(cdist(clean, clean, scorer=JaroWinkler.similarity), dtype=np.float32)
        elif scorer_name == "levenshtein":
            matrix = np.asarray(cdist(clean, clean, scorer=Levenshtein.normalized_similarity), dtype=np.float32)
        else:
            matrix = np.asarray(cdist(clean, clean, scorer=token_sort_ratio), dtype=np.float32) / 100.0
    elif scorer_name == "date":
        # Date-aware scorer (#1858). This find_fuzzy_matches / polars-direct path
        # stays pure-Python: score_field_matrix's native id namespace already uses
        # 4 for soundex_match, whereas the bucket path (score_one id 4) DOES route
        # date through the kernel. Both agree by construction -- _date_similarity_py
        # mirrors score-core::date_similarity (native-parity asserted). Dates land
        # in small same-key blocks, so the O(n^2) Python loop is not a hot path.
        n = len(clean)
        matrix = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            for j in range(i + 1, n):
                s = _date_similarity_py(clean[i], clean[j])
                matrix[i, j] = matrix[j, i] = s
    elif scorer_name == "date_diff":
        # Magnitude-aware date comparator (spec 2026-07-23). Same O(n^2) shape as
        # `date` -- dates land in small same-key blocks, so this is not a hot path,
        # and _field_score_matrix_dedup collapses distinct values. Calling the
        # SAME scalar fn guarantees scalar == vectorized parity by construction.
        n = len(clean)
        matrix = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            for j in range(i + 1, n):
                s = _date_diff_similarity_py(clean[i], clean[j])
                matrix[i, j] = matrix[j, i] = s
    elif scorer_name == "geo_haversine":
        # Great-circle distance comparator (spec 2026-07-23). Same O(n^2) shape
        # as `date`/`date_diff` -- coordinates land in small same-key blocks, and
        # _field_score_matrix_dedup collapses distinct values. Calling the SAME
        # scalar fn guarantees scalar == vectorized parity by construction.
        n = len(clean)
        matrix = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            for j in range(i + 1, n):
                s = _geo_haversine_similarity_py(clean[i], clean[j])
                matrix[i, j] = matrix[j, i] = s
    elif scorer_name == "numeric_diff" or scorer_name.startswith("numeric_diff:"):
        # Magnitude-aware numeric comparator (spec 2026-07-23). Same O(n^2) shape;
        # the band spec rides on the scorer string, passed through to the SAME
        # scalar fn -> scalar == vectorized parity by construction.
        n = len(clean)
        matrix = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            for j in range(i + 1, n):
                s = _numeric_diff_similarity_py(clean[i], clean[j], scorer_name)
                matrix[i, j] = matrix[j, i] = s
    elif scorer_name == "initialism_match":
        return _initialism_score_matrix(values)
    elif scorer_name == "alias_match":
        return _alias_score_matrix(values)
    elif scorer_name == "dice":
        return _dice_score_matrix(values)
    elif scorer_name == "jaccard":
        return _jaccard_score_matrix(values)
    elif scorer_name == "phash":
        return _phash_score_matrix(values)
    elif scorer_name == "audio_fp":
        return _audio_fp_score_matrix(values)
    elif scorer_name == "radial":
        return _radial_score_matrix(values)
    elif scorer_name == "qgram":
        return _qgram_score_matrix(values)
    else:
        # Plugin scorer fallback. Two contracts:
        # 1. Plugin exposes ``score_matrix(values) -> np.ndarray`` — vectorized
        #    NxN scorer. Used for hot paths (find_fuzzy_matches scores up to
        #    ~12M pairs per 5000-row block; a Python double-loop turns rapidfuzz
        #    cdist's millisecond scan into seconds-to-minutes).
        # 2. Plugin only exposes ``score_pair(a, b)`` — fall back to a Python
        #    double-loop. Acceptable for plugin scorers used on small blocks or
        #    in non-hot paths; refuse silently large N here so a future user
        #    doesn't hit the wall-time landmine without a hint.
        from goldenmatch.plugins.registry import PluginRegistry

        plugin = PluginRegistry.instance().get_scorer(scorer_name)
        if plugin is None:
            raise ValueError(f"Unknown fuzzy scorer: {scorer_name!r}")
        matrix_fn = getattr(plugin, "score_matrix", None)
        if matrix_fn is not None:
            # Pass the per-field tf table only if the plugin's score_matrix
            # accepts it (back-compat: most plugins are score_matrix(values)).
            try:
                matrix = np.asarray(matrix_fn(values, tf_freqs=tf_freqs), dtype=np.float32)
            except TypeError:
                matrix = np.asarray(matrix_fn(values), dtype=np.float32)
        else:
            n = len(values)
            if n > 1000:
                logger.warning(
                    "plugin scorer %r has no score_matrix() method; "
                    "falling back to O(N^2) score_pair loop over %d values "
                    "(~%d calls). Expect wall-time impact on large blocks.",
                    scorer_name, n, n * (n - 1) // 2,
                )
            out = np.zeros((n, n), dtype=np.float32)
            for i in range(n):
                vi = values[i]
                for j in range(i + 1, n):
                    s = plugin.score_pair(vi, values[j])  # pyright: ignore[reportAttributeAccessIssue]
                    out[i, j] = out[j, i] = 0.0 if s is None else float(s)
            np.fill_diagonal(out, 1.0)
            matrix = out

    return np.asarray(matrix, dtype=np.float64)


def _record_embedding_score_matrix(
    block_df: pl.DataFrame, columns: list[str], model_name: str = "all-MiniLM-L6-v2",
    column_weights: dict[str, float] | None = None,
) -> np.ndarray:
    """NxN score matrix from record-level embeddings.

    Concatenates columns into a single text string per record,
    embeds the full string, and computes cosine similarity.
    """
    from goldenmatch.core.embedder import get_embedder

    concat_values = []
    for row in block_df.iter_rows(named=True):
        parts = []
        for col in columns:
            if column_weights is not None:
                w = column_weights.get(col, 1.0)
                if w <= 0:
                    continue
                val = row.get(col)
                if val is not None:
                    part = f"{col}: {val}"
                    repeats = round(w) if w > 1.0 else 1
                    for _ in range(repeats):
                        parts.append(part)
            else:
                val = row.get(col)
                if val is not None:
                    parts.append(f"{col}: {val}")
        concat_values.append(" | ".join(parts) if parts else "")

    from goldenmatch.core.frame import to_frame as _tf_emb

    row_ids = _tf_emb(block_df).column("__row_id__").to_list()
    cache_key = f"_rec_emb_{hash(tuple(columns))}_{hash(tuple(row_ids))}"

    embedder = get_embedder(model_name)
    embeddings = embedder.embed_column(concat_values, cache_key=cache_key)
    sim = embedder.cosine_similarity_matrix(embeddings)
    return np.asarray(sim, dtype=np.float64)


def _soundex_score_single(val_a: str, val_b: str) -> float:
    """Per-pair ``soundex_match``: 1.0 iff a NON-EMPTY canonical soundex code is
    shared. Byte-for-byte with score-core ``soundex_match`` (id 6) -- the
    empty-code guard means garbage/empty never matches (no placeholder
    mega-clustering)."""
    ca = canonical_soundex(val_a)
    return 1.0 if ca and ca == canonical_soundex(val_b) else 0.0


def _soundex_score_matrix(values: list) -> np.ndarray:
    """NxN soundex match matrix.

    Tries the native kernel (Rust soundex + symmetric pairwise compare) first
    so the row-loop + per-row jellyfish.soundex Python overhead drops out at
    scale. Falls back to the hash-group exact-match path when native isn't
    available -- functionally identical, just slower at large N.
    """
    m = _native_field_matrix(values, "soundex_match")
    if m is not None:
        return m
    # Empty code (no phonetic content) -> None so it matches NOTHING in the
    # exact-match matrix, mirroring score-core `soundex_match`'s empty-guard.
    codes = [(canonical_soundex(v) or None) if v is not None else None for v in values]
    return _exact_score_matrix(codes)


def _initialism_score_matrix(values: list) -> np.ndarray:
    """NxN initialism-match matrix (no native kernel).

    The match is a CROSS comparison of raw-string vs derived-initialism, not a
    single canonical key per row, so this can't reduce to a hash-group like
    ``_soundex_score_matrix``. Blocks reaching this path are small, so a direct
    double-loop over the pairwise scorer is the safest correct impl — and it is
    parity-checked against the pairwise ``score_field`` in
    ``tests/test_semantic_scorers.py``.
    """
    n = len(values)
    scores = np.zeros((n, n))
    for i in range(n):
        vi = values[i]
        if vi is None:
            continue
        for j in range(i, n):
            vj = values[j]
            if vj is None:
                continue
            s = _initialism_match_single(vi, vj)
            scores[i, j] = scores[j, i] = s
    return scores


def _alias_score_matrix(values: list) -> np.ndarray:
    """NxN alias-match matrix (no native kernel).

    Each row HAS a single canonical key per alias space, so this groups like
    ``_soundex_score_matrix``: a pair matches iff it shares a NON-EMPTY
    business canonical OR a non-empty given-name canonical. Two independent
    hash-groupings (one per space) unioned together reproduce the pairwise
    OR — and stay byte-identical to the pairwise ``score_field`` (parity-
    checked in ``tests/test_semantic_scorers.py``).
    """
    from goldenmatch.refdata.business_aliases import canonical_company_form
    from goldenmatch.refdata.given_names import canonical_form

    # Empty canonicals are dropped (never group) so the empty guard holds.
    biz = [
        (canonical_company_form(v) or "") if v is not None else "" for v in values
    ]
    given = [(canonical_form(v) or "") if v is not None else "" for v in values]
    scores = _exact_score_matrix([k or None for k in biz])
    given_scores = _exact_score_matrix([k or None for k in given])
    return np.maximum(scores, given_scores)


# ---------------------------------------------------------------------------
# PPRL (Privacy-Preserving Record Linkage) scoring
# ---------------------------------------------------------------------------

def _hex_to_bits(hex_str: str) -> np.ndarray:
    """Convert hex-encoded bloom filter to a numpy uint8 byte array."""
    return np.frombuffer(bytes.fromhex(hex_str), dtype=np.uint8)


def _pad_to_equal_length(
    bits_a: np.ndarray, bits_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Zero-pad the shorter of two uint8 byte arrays up to the longer length.

    Mirrors the ``max_len`` zero-padding in ``_dice_score_matrix`` /
    ``_jaccard_score_matrix`` so the single-pair helpers agree with the batch
    path on the same inputs. Without it, ``np.bitwise_and`` on different-length
    arrays raises an opaque broadcast ``ValueError`` (issue #784). Padding with
    zero bytes is bit-identical to bit-level padding (popcount is unchanged by
    trailing zeros), and AND/OR against an implicit-zero tail is well defined.
    """
    len_a, len_b = len(bits_a), len(bits_b)
    if len_a == len_b:
        return bits_a, bits_b
    max_len = max(len_a, len_b)
    if len_a < max_len:
        bits_a = np.concatenate([bits_a, np.zeros(max_len - len_a, dtype=np.uint8)])
    else:
        bits_b = np.concatenate([bits_b, np.zeros(max_len - len_b, dtype=np.uint8)])
    return bits_a, bits_b


def _dice_score_single(val_a: str, val_b: str) -> float:
    """Dice coefficient on two hex-encoded bloom filters."""
    bits_a, bits_b = _pad_to_equal_length(_hex_to_bits(val_a), _hex_to_bits(val_b))
    intersection = np.unpackbits(np.bitwise_and(bits_a, bits_b)).sum()
    total = np.unpackbits(bits_a).sum() + np.unpackbits(bits_b).sum()
    return float(2.0 * intersection / total) if total > 0 else 0.0


def _jaccard_score_single(val_a: str, val_b: str) -> float:
    """Jaccard similarity on two hex-encoded bloom filters."""
    bits_a, bits_b = _pad_to_equal_length(_hex_to_bits(val_a), _hex_to_bits(val_b))
    intersection = np.unpackbits(np.bitwise_and(bits_a, bits_b)).sum()
    union = np.unpackbits(np.bitwise_or(bits_a, bits_b)).sum()
    return float(intersection / union) if union > 0 else 0.0


def _dice_score_matrix(values: list) -> np.ndarray:
    """NxN Dice coefficient matrix on hex-encoded bloom filters.

    Uses vectorized numpy operations: unpack all bloom filters to bits,
    compute intersection via matrix multiply, and popcount via sum.
    """
    n = len(values)
    # Convert hex strings to bit matrix (n, filter_size_bits)
    bit_arrays = []
    for v in values:
        if v is not None:
            bit_arrays.append(np.unpackbits(_hex_to_bits(v)))
        else:
            bit_arrays.append(np.zeros(0, dtype=np.uint8))

    # Handle variable-length or empty arrays
    if not bit_arrays or len(bit_arrays[0]) == 0:
        return np.zeros((n, n))

    max_len = max(len(b) for b in bit_arrays)
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)
    for i, b in enumerate(bit_arrays):
        if len(b) > 0:
            bit_matrix[i, :len(b)] = b

    # Intersection: dot product of bit vectors
    intersection = bit_matrix @ bit_matrix.T  # (n, n)

    # Popcount per vector
    popcounts = bit_matrix.sum(axis=1)  # (n,)

    # Dice: 2*|A&B| / (|A| + |B|)
    denom = popcounts[:, None] + popcounts[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        dice = np.where(denom > 0, 2.0 * intersection / denom, 0.0)

    return dice.astype(np.float64)


def _jaccard_score_matrix(values: list) -> np.ndarray:
    """NxN Jaccard similarity matrix on hex-encoded bloom filters."""
    n = len(values)
    bit_arrays = []
    for v in values:
        if v is not None:
            bit_arrays.append(np.unpackbits(_hex_to_bits(v)))
        else:
            bit_arrays.append(np.zeros(0, dtype=np.uint8))

    if not bit_arrays or len(bit_arrays[0]) == 0:
        return np.zeros((n, n))

    max_len = max(len(b) for b in bit_arrays)
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)
    for i, b in enumerate(bit_arrays):
        if len(b) > 0:
            bit_matrix[i, :len(b)] = b

    intersection = bit_matrix @ bit_matrix.T
    popcounts = bit_matrix.sum(axis=1)
    # Union: |A| + |B| - |A&B|
    union = popcounts[:, None] + popcounts[None, :] - intersection
    with np.errstate(divide="ignore", invalid="ignore"):
        jaccard = np.where(union > 0, intersection / union, 0.0)

    return jaccard.astype(np.float64)


def _norm_phash_hex(s: str) -> str:
    """Strip an optional ``0x`` prefix and left-pad to an even number of hex
    digits so ``bytes.fromhex`` accepts it (image pHash is 16 hex chars = 64 bits;
    ``hex(h)`` output is tolerated)."""
    if s[:2] in ("0x", "0X"):
        s = s[2:]
    return ("0" + s) if len(s) % 2 else s


def _phash_score_single(val_a: str, val_b: str) -> float:
    """Hamming similarity of two hex perceptual hashes: ``1 - dist / bits``."""
    bits_a, bits_b = _pad_to_equal_length(
        _hex_to_bits(_norm_phash_hex(val_a)), _hex_to_bits(_norm_phash_hex(val_b))
    )
    nbits = bits_a.size * 8
    if nbits == 0:
        return 0.0
    dist = int(np.unpackbits(np.bitwise_xor(bits_a, bits_b)).sum())
    return 1.0 - dist / nbits


def _phash_score_matrix(values: list) -> np.ndarray:
    """NxN hamming-similarity matrix on hex perceptual hashes.

    ``hamming = |A| + |B| - 2*|A&B|`` over the unpacked bits; similarity is
    ``1 - hamming / bits``. None / unparseable values score 0 against everything.
    """
    n = len(values)
    bit_arrays: list[np.ndarray] = []
    valid = np.zeros(n, dtype=bool)
    for i, v in enumerate(values):
        if v is not None:
            try:
                bit_arrays.append(np.unpackbits(_hex_to_bits(_norm_phash_hex(v))))
                valid[i] = True
                continue
            except ValueError:
                pass
        bit_arrays.append(np.zeros(0, dtype=np.uint8))

    if not valid.any():
        return np.zeros((n, n))

    max_len = max(len(b) for b in bit_arrays)
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)
    for i, b in enumerate(bit_arrays):
        if len(b) > 0:
            bit_matrix[i, : len(b)] = b

    intersection = bit_matrix @ bit_matrix.T
    popcounts = bit_matrix.sum(axis=1)
    hamming = popcounts[:, None] + popcounts[None, :] - 2.0 * intersection
    sim = 1.0 - hamming / max_len
    sim = np.where(valid[:, None] & valid[None, :], sim, 0.0)
    return sim.astype(np.float64)


def _audio_fp_score_single(val_a: str, val_b: str) -> float:
    """Offset-aligned similarity (``1 - best BER``) of two hex audio fingerprints."""
    from goldenmatch.core.perceptual import audio_ber_aligned, audio_fp_from_hex

    return 1.0 - audio_ber_aligned(audio_fp_from_hex(val_a), audio_fp_from_hex(val_b))


def _audio_fp_score_matrix(values: list) -> np.ndarray:
    """NxN offset-aligned audio-fingerprint similarity. Audio fingerprints are
    variable-length with an alignment search, so this is a symmetric pairwise loop
    (block-sized N); None values score 0 against everything."""
    from goldenmatch.core.perceptual import audio_ber_aligned, audio_fp_from_hex

    n = len(values)
    parsed = [audio_fp_from_hex(v) if v is not None else None for v in values]
    out = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        fp_i = parsed[i]
        if fp_i is None:
            continue
        out[i, i] = 1.0
        for j in range(i + 1, n):
            fp_j = parsed[j]
            if fp_j is None:
                continue
            sim = 1.0 - audio_ber_aligned(fp_i, fp_j)
            out[i, j] = out[j, i] = sim
    return out


def _radial_score_single(val_a: str, val_b: str) -> float:
    """Rotation-aligned similarity of two radial-variance profiles (ADR 0022,
    finding 1). Parses the hex column form and runs the best-cyclic-shift Pearson
    -- the geometric counterpart to ``phash`` (which is photometric-only)."""
    from goldenmatch.core.perceptual import radial_align_similarity, radial_from_hex

    return radial_align_similarity(radial_from_hex(val_a), radial_from_hex(val_b))


def _radial_score_matrix(values: list) -> np.ndarray:
    """NxN rotation-aligned radial-variance similarity. The compare is an angular
    alignment search (not vectorizable like a bit-hamming), so this is a symmetric
    pairwise loop (block-sized N); None / unparseable values score 0 everywhere."""
    from goldenmatch.core.perceptual import radial_align_similarity, radial_from_hex

    n = len(values)
    parsed: list[list[int] | None] = []
    for v in values:
        if v is None:
            parsed.append(None)
            continue
        prof = radial_from_hex(v)
        parsed.append(prof if prof else None)
    out = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        prof_i = parsed[i]
        if prof_i is None:
            continue
        out[i, i] = 1.0
        for j in range(i + 1, n):
            prof_j = parsed[j]
            if prof_j is None:
                continue
            sim = radial_align_similarity(prof_i, prof_j)
            out[i, j] = out[j, i] = sim
    return out


def _qgram_set(s: str, n: int = 3) -> set[str]:
    """Padded character-n-gram set of a raw string.

    Lowercases and pads with ``n-1`` ``#`` sentinels on each side (so a
    3-gram view of ``"abc"`` is ``{"##a", "#ab", "abc", "bc#", "c##"}``)
    then returns the FULL set of length-``n`` substrings -- no truncation
    (unlike the lossy ``qgram:N`` *transform*, which keeps only ``[:5]``).
    """
    s = s.lower()
    pad = "#" * (n - 1)
    padded = pad + s + pad
    return {padded[i : i + n] for i in range(len(padded) - n + 1)}


def _qgram_score_single(val_a: str, val_b: str, n: int = 3) -> float:
    """Character-n-gram Jaccard similarity on two raw strings.

    Returns 1.0 when the strings are identical (incl. both empty), 0.0 when
    the q-gram union is empty (one side empty, the other not), else the
    Jaccard ratio ``|A & B| / |A | B|`` over their padded q-gram sets.
    """
    if val_a == val_b:
        return 1.0
    set_a = _qgram_set(val_a, n)
    set_b = _qgram_set(val_b, n)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _ensemble_score_single(val_a: str, val_b: str) -> float:
    """Per-pair ensemble: ``max(jaro_winkler, token_sort/100, soundex*0.8)``.

    Float64 twin of the ``ensemble`` branch in ``_fuzzy_score_matrix`` and of
    ``score_field(a, b, "ensemble")`` (same three components, same 0.8 soundex
    bonus). Extracted as a standalone callable so the bucket fast path can
    dispatch ensemble per-pair (behind ``GOLDENMATCH_ENSEMBLE_KERNEL``) instead
    of declining to the float32 matrix path. See
    ``docs/superpowers/specs/2026-07-21-ensemble-kernel-measurement.md`` for the
    recall-parity measurement that governs whether this is default-on.

    NOTE the dtype: the matrix path (``_fuzzy_score_matrix``) computes each
    component AND the weighted matchkey combine in float32; this returns float64.
    A borderline pair near ``>= threshold`` can flip between the two, which is
    exactly the divergence the ensemble decline warned about. The optional
    ``GOLDENMATCH_ENSEMBLE_KERNEL=f32`` mode casts the per-pair score to float32
    to quantize it like the matrix's per-field values (parity experiment).
    """
    jw = JaroWinkler.similarity(val_a, val_b)
    ts = token_sort_ratio(val_a, val_b) / 100.0
    try:
        ca = canonical_soundex(val_a)
        sx = 0.8 if ca and ca == canonical_soundex(val_b) else 0.0
    except Exception:
        sx = 0.0
    return max(jw, ts, sx)


def _ensemble_score_single_f32(  # pyright: ignore[reportUnusedFunction]  # consumed via a lazy cross-module import in backends/score_buckets.py
    val_a: str, val_b: str
) -> float:
    """``_ensemble_score_single`` with the result quantized to float32.

    Mirrors the matrix path's per-field float32 storage: each ensemble component
    matrix is ``astype(np.float32)`` before the ``np.maximum`` combine, so the
    per-field value that enters the weighted combine is float32-quantized. This
    variant reproduces that quantization at the per-pair boundary (the combine
    itself stays float64 in the fast path, but the dominant rounding is here).
    """
    return float(np.float32(_ensemble_score_single(val_a, val_b)))


def _qgram_score_matrix(values: list, n: int = 3) -> np.ndarray:
    """NxN character-n-gram Jaccard matrix on raw strings.

    Clear O(N^2) loop. This is the matrix fallback: qgram configs now route
    through the bucket backend, which scores via the score-core kernel
    (``score_one`` id 5) or its byte-identical per-pair mirror
    ``_qgram_score_single``; this matrix path serves the non-bucket
    (``find_fuzzy_matches``) route and is the parity reference. None values
    score 0.0 against everything (including the diagonal), mirroring how the
    bloom matrices treat missing values.
    """
    size = len(values)
    out = np.zeros((size, size), dtype=np.float64)
    grams: list[set[str] | None] = [
        _qgram_set(v, n) if v is not None else None for v in values
    ]
    for i in range(size):
        gi = grams[i]
        if gi is None:
            continue
        out[i, i] = 1.0
        for j in range(i + 1, size):
            gj = grams[j]
            if gj is None:
                continue
            if values[i] == values[j]:
                s = 1.0
            else:
                union = gi | gj
                s = len(gi & gj) / len(union) if union else 0.0
            out[i, j] = out[j, i] = s
    return out


def _build_null_mask(values: list) -> np.ndarray:
    """NxN boolean mask — True where either value is null."""
    null_arr = np.array([v is None for v in values])
    return null_arr[:, None] | null_arr[None, :]


def find_fuzzy_matches(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
    pre_scored_pairs: list[tuple[int, int, float]] | None = None,
    *,
    _emit_dataframe: bool = False,
) -> list[tuple[int, int, float]] | pl.DataFrame:
    """Find fuzzy matches within a block DataFrame.

    Uses vectorized rapidfuzz cdist for batch scoring, with early termination
    when exact fields make it mathematically impossible to reach the threshold.

    Args:
        block_df: Block DataFrame with __row_id__ and field columns.
        mk: Matchkey configuration with fields, weights, and threshold.
        exclude_pairs: Optional set of (min_id, max_id) pairs to skip.
        pre_scored_pairs: Optional pre-computed (id_a, id_b, score) pairs
            from ANN blocking. When set, skip NxN scoring.
        _emit_dataframe: Arrow-native roadmap Phase 1 opt-in. When True,
            EVERY return branch (early-empty, ``pre_scored_pairs``,
            negative-evidence penalty, ``exclude_pairs``, and the hot
            path) emits a ``pl.DataFrame`` with ``PAIR_STREAM_SCHEMA``
            instead of a Python list of tuples. On the hot path the
            frame is built directly from numpy arrays (zero per-pair
            Python overhead — the bottleneck for 200M-pair / 5M-row runs
            per the Phase 1 spec); the non-hot branches build their
            filtered result list then convert once at the boundary via
            ``pairs_list_to_df`` (Task 1.1, #623). Default False keeps
            the legacy ``list[tuple]`` contract as the default opt-in
            path alongside the columnar output path. The arg is
            keyword-only via the ``*`` marker so legacy callers don't
            accidentally pass it.

    Returns:
        ``list[tuple[int, int, float]]`` by default (legacy contract).
        ``pl.DataFrame`` with ``PAIR_STREAM_SCHEMA`` columns from ALL
        branches when ``_emit_dataframe=True``.
    """
    # find_fuzzy_matches requires mk.threshold + field weights/scorers set;
    # upstream config validation enforces this. Pyright sees the schema-level
    # Optional, so we narrow once here for clarity.
    assert mk.threshold is not None, "find_fuzzy_matches requires mk.threshold"
    mk_threshold: float = mk.threshold

    # Task 1.1 (#623): emit a uniform return shape across ALL branches.
    # When ``_emit_dataframe`` is True every branch (early-empty,
    # pre_scored_pairs, NE-penalty, exclude_pairs, hot path) returns a
    # ``pl.DataFrame`` with ``PAIR_STREAM_SCHEMA``; otherwise the legacy
    # ``list[tuple]`` path (permanent opt-in, default). These two helpers
    # keep the branch bodies readable and the conversion in one place.
    def _emit_empty() -> list[tuple[int, int, float]] | pl.DataFrame:
        return _empty_pair_stream_df() if _emit_dataframe else []

    def _emit_results(
        results: list[tuple[int, int, float]],
    ) -> list[tuple[int, int, float]] | pl.DataFrame:
        if not _emit_dataframe:
            return results
        return pairs_list_to_df(results)

    # Fast path: pre-scored pairs from ANN (skip NxN scoring)
    if pre_scored_pairs is not None:
        results = []
        for a, b, score in pre_scored_pairs:
            if score >= mk_threshold:
                pair_key = (min(a, b), max(a, b))
                if exclude_pairs and pair_key in exclude_pairs:
                    continue
                results.append((pair_key[0], pair_key[1], score))
        return _emit_results(results)

    from goldenmatch.core.frame import to_frame as _to_frame_d5

    n = _to_frame_d5(block_df).height
    if n < 2:
        return _emit_empty()

    row_ids = _to_frame_d5(block_df).column("__row_id__").to_list()

    # Separate exact (cheap), record_embedding, and fuzzy (expensive) fields.
    # initialism_match / alias_match are equality-style (1.0/0.0) scorers with
    # no NxN compute cost beyond a key derivation, so they ride the cheap path
    # alongside exact/soundex (and are excluded from the fuzzy partition for
    # the early-termination short-circuit).
    _EQUALITY_SCORERS = ("exact", "soundex_match", "initialism_match", "alias_match")
    exact_fields = [f for f in mk.fields if f.scorer in _EQUALITY_SCORERS]
    record_emb_fields = [f for f in mk.fields if f.scorer == "record_embedding"]
    fuzzy_fields = [
        f for f in mk.fields
        if f.scorer not in _EQUALITY_SCORERS and f.scorer != "record_embedding"
    ]

    # All scoring-path MatchkeyFields are upstream-validated to have weight set;
    # narrow with cast helper so the schema-level Optional doesn't poison every
    # sum(). Runtime behavior unchanged: if weight is None, the subsequent
    # arithmetic raises TypeError exactly as today.
    from typing import cast as _cast

    def _w(f: MatchkeyField) -> float:
        return _cast(float, f.weight)

    total_weight = sum(_w(f) for f in mk.fields)
    if total_weight == 0.0:
        return _emit_empty()

    # Phase 1: Score cheap fields (exact + soundex) and build null masks.
    #
    # `dtype=np.float32` not the numpy default float64 — the find_fuzzy_matches
    # function holds ~8-10 NxN arrays in scope at peak (cheap_num/den,
    # max_poss_num/den/result, fuzzy_num/den, per-field scores, plus
    # short-lived best_*/total_* in the early-termination loop). At a
    # 5000-row block that's ~1.6 GB per call in float64; float32 halves it.
    # With 4 parallel workers in `score_blocks_parallel`, 1M-row runs were
    # hitting Windows's effective ~5 GB contiguous-allocation ceiling and
    # dying via SystemError / MemoryError / silent crash (PR #173, scale
    # audit). Scores are 0-1 similarities — float32's 7 digits of precision
    # is well within tolerance.
    cheap_numerator = np.zeros((n, n), dtype=np.float32)
    cheap_denominator = np.zeros((n, n), dtype=np.float32)

    for f in exact_fields:
        values = _get_transformed_values(block_df, f)
        null_mask = _build_null_mask(values)
        valid = ~null_mask

        if f.scorer == "exact":
            scores = _exact_score_matrix(values)
        elif f.scorer == "soundex_match":
            scores = _soundex_score_matrix(values)
        elif f.scorer == "initialism_match":
            scores = _initialism_score_matrix(values)
        else:  # alias_match
            scores = _alias_score_matrix(values)

        cheap_numerator += scores * _w(f) * valid
        cheap_denominator += _w(f) * valid

    # Phase 2: Early termination check
    # For each pair, the maximum possible score is:
    #   (cheap_contribution + fuzzy_max_weight) / (cheap_denom + fuzzy_max_weight)
    # where fuzzy_max_weight assumes all fuzzy fields score 1.0
    fuzzy_total_weight = sum(_w(f) for f in fuzzy_fields) + sum(_w(f) for f in record_emb_fields)

    # If no fuzzy or record_embedding fields, just use cheap scores
    if not fuzzy_fields and not record_emb_fields:
        with np.errstate(divide="ignore", invalid="ignore"):
            combined = np.where(cheap_denominator > 0, cheap_numerator / cheap_denominator, 0.0)
    else:
        # Check which pairs can possibly reach threshold even if all fuzzy fields score 1.0
        max_possible_numerator = cheap_numerator + fuzzy_total_weight
        max_possible_denominator = cheap_denominator + fuzzy_total_weight

        with np.errstate(divide="ignore", invalid="ignore"):
            max_possible = np.where(
                max_possible_denominator > 0,
                max_possible_numerator / max_possible_denominator,
                0.0,
            )

        # Pairs that can't possibly reach threshold — mark them
        impossible = max_possible < mk_threshold

        # Phase 3: Score fuzzy fields with intra-field early termination.
        # float32 — matches Phase 1's accumulators (see comment there).
        fuzzy_numerator = np.zeros((n, n), dtype=np.float32)
        fuzzy_denominator = np.zeros((n, n), dtype=np.float32)

        all_expensive_fields = list(fuzzy_fields) + list(record_emb_fields)
        # WARNING: do NOT add `with stage()` inside this loop. The
        # bench harness's `add_timing` does a dict write under the
        # GIL; with 4 worker threads in score_blocks_parallel, those
        # writes contend with rapidfuzz's GIL release and slow the
        # whole pipeline by ~5x (measured: 24s no-bench vs 127s with
        # per-scorer stages). Stage wrappers at the pipeline level
        # are fine (single main thread, written once per stage).
        for f_idx, f in enumerate(all_expensive_fields):
            if f.scorer == "record_embedding":
                try:
                    scores = _record_embedding_score_matrix(
                        block_df, f.columns or [], model_name=f.model or "all-MiniLM-L6-v2",
                        column_weights=f.column_weights,
                    )
                except Exception:
                    logger.warning(
                        "Record embedding scorer failed for columns %s, falling back to token_sort",
                        f.columns, exc_info=True,
                    )
                    concat_values = []
                    for row in block_df.to_dicts():
                        parts = [str(row.get(c, "") or "") for c in (f.columns or [])]
                        concat_values.append(" ".join(parts))
                    scores = _fuzzy_score_matrix(concat_values, "token_sort")
                # #1859: mask the record_embedding contribution like every other
                # field. A row is unobserved on this field iff ALL its columns
                # are null; a pair counts the field only when BOTH sides are
                # observed. Without this, an unobserved record_embedding always
                # added its full weight to the DENOMINATOR (diluting the score)
                # -- the #1856 shape on a first-class weighted scorer. Clean data
                # (no all-null emb rows) is unaffected: valid is then all-True.
                emb_cols = f.columns or []
                emb_row_null = np.ones(n, dtype=bool)
                for c in emb_cols:
                    col_vals = (
                        block_df[c].to_list() if c in block_df.columns else [None] * n
                    )
                    emb_row_null &= np.array([v is None for v in col_vals])
                emb_valid = ~(emb_row_null[:, None] | emb_row_null[None, :])
                fuzzy_numerator += scores * _w(f) * emb_valid
                fuzzy_denominator += _w(f) * emb_valid
            else:
                values = _get_transformed_values(block_df, f)
                null_mask = _build_null_mask(values)
                valid = ~null_mask

                assert f.scorer is not None, "fuzzy field scorer must be set"
                scores = _fuzzy_score_matrix(
                    values, f.scorer, model_name=f.model or "all-MiniLM-L6-v2",
                    tf_freqs=getattr(f, "tf_freqs", None),
                )

                fuzzy_numerator += scores * _w(f) * valid
                fuzzy_denominator += _w(f) * valid

            # Intra-field early termination: if no pair can reach threshold
            # even with perfect scores on all remaining fields, stop early
            remaining_weight = sum(
                _w(all_expensive_fields[i])
                for i in range(f_idx + 1, len(all_expensive_fields))
            )
            if remaining_weight > 0:
                total_num = cheap_numerator + fuzzy_numerator
                total_den = cheap_denominator + fuzzy_denominator
                # Best case: remaining fields all score 1.0
                best_num = total_num + remaining_weight
                best_den = total_den + remaining_weight
                with np.errstate(divide="ignore", invalid="ignore"):
                    best_possible = np.where(best_den > 0, best_num / best_den, 0.0)
                # Apply existing impossible mask
                best_possible[impossible] = 0.0
                # Only check upper triangle
                best_upper = np.triu(best_possible, k=1)
                if np.max(best_upper) < mk_threshold:
                    break  # No pair can reach threshold, skip remaining fields

        # Combine cheap + fuzzy
        total_numerator = cheap_numerator + fuzzy_numerator
        total_denominator = cheap_denominator + fuzzy_denominator

        with np.errstate(divide="ignore", invalid="ignore"):
            combined = np.where(total_denominator > 0, total_numerator / total_denominator, 0.0)

        # Zero out impossible pairs (early termination)
        combined[impossible] = 0.0

    # Extract upper triangle pairs above threshold using numpy
    # Zero out lower triangle and diagonal
    upper = np.triu(combined, k=1)
    rows_idx, cols_idx = np.where(upper >= mk_threshold)

    if len(rows_idx) == 0:
        return _emit_empty()

    row_id_arr = np.array(row_ids)
    ids_a = row_id_arr[rows_idx]
    ids_b = row_id_arr[cols_idx]
    scores = upper[rows_idx, cols_idx]

    # v1.11: Apply negative-evidence penalty for weighted matchkeys.
    # NE is per-pair (not vectorized), applied only when mk.negative_evidence is set.
    if mk.negative_evidence:
        # Dual-rep: block_df may be a pl.DataFrame (to_dicts) or a pa.Table
        # (to_pylist) on the arrow lane -- both yield a list[dict] of rows.
        block_rows = (
            block_df.to_dicts() if hasattr(block_df, "to_dicts")
            else block_df.to_pylist()  # pyright: ignore[reportAttributeAccessIssue]
        )
        ne_scores = []
        for i, j, s in zip(rows_idx, cols_idx, scores):
            row_a = block_rows[int(i)]
            row_b = block_rows[int(j)]
            ne_pair = {col: (row_a.get(col), row_b.get(col)) for col in row_a}
            penalty = _apply_negative_evidence(mk, ne_pair)
            final_s = max(0.0, float(s) - penalty)
            ne_scores.append(final_s)
        scores = ne_scores
        # Re-filter: only keep pairs whose adjusted score meets threshold
        if exclude_pairs is not None and len(exclude_pairs) > 0:
            results = []
            for a, b, s in zip(ids_a, ids_b, scores):
                if s < mk_threshold:
                    continue
                pair_key = (min(int(a), int(b)), max(int(a), int(b)))
                if pair_key not in exclude_pairs:
                    results.append((int(a), int(b), float(s)))
            return _emit_results(results)
        return _emit_results(
            [(int(a), int(b), float(s)) for a, b, s in zip(ids_a, ids_b, scores)
             if s >= mk_threshold]
        )

    if exclude_pairs is not None and len(exclude_pairs) > 0:
        results = []
        for a, b, s in zip(ids_a, ids_b, scores):
            pair_key = (min(int(a), int(b)), max(int(a), int(b)))
            if pair_key not in exclude_pairs:
                results.append((int(a), int(b), float(s)))
        return _emit_results(results)

    # HOT PATH: no NE, no exclude_pairs, no pre_scored_pairs. ~99% of
    # production block-scoring calls land here. The Phase 1c
    # _emit_dataframe opt-in (#623) bypasses the list-of-tuples
    # construction by building a Polars DataFrame directly from the
    # numpy arrays. List comprehension at 200M pairs (5M-row reference
    # shape) is the per-pair Python overhead the Arrow-native roadmap
    # exists to remove; this is where it lands.
    if _emit_dataframe:
        return pl.DataFrame({
            "id_a": ids_a if hasattr(ids_a, "astype") else np.asarray(ids_a, dtype=np.int64),
            "id_b": ids_b if hasattr(ids_b, "astype") else np.asarray(ids_b, dtype=np.int64),
            "score": scores if hasattr(scores, "astype") else np.asarray(scores, dtype=np.float64),
        }, schema=_pair_stream_schema())
    return [(int(a), int(b), float(s)) for a, b, s in zip(ids_a, ids_b, scores)]


# ---------------------------------------------------------------------------
# Parallel block scoring
# ---------------------------------------------------------------------------

def _score_one_block(
    block: Any,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    *,
    _emit_dataframe: bool = False,
) -> list[tuple[int, int, float]] | pl.DataFrame:
    """Score a single block — safe to call from a thread.

    Task 1.2 (#623): ``_emit_dataframe`` threads through to
    ``find_fuzzy_matches`` so the block scorer can be DataFrame-canonical.
    When True the return is a ``pl.DataFrame`` (``PAIR_STREAM_SCHEMA``)
    and the ``across_files_only`` cross-source filter is applied via a
    Polars ``.filter()`` on the frame rather than a Python list
    comprehension. Default False keeps the legacy list contract as the
    permanent opt-in alongside the default ``list[tuple]`` path.

    NOTE (W2c): the across-files filter is shared with
    ``_score_one_block_columnar`` via ``_cross_source_filter_df`` -- the old
    "mirror any change" rule is retired.
    """
    block_df = block.materialize().native

    if across_files_only and source_lookup:
        from goldenmatch.core.frame import to_frame as _to_frame_d5

        sources_in_block = (
            _to_frame_d5(block_df).column("__source__").unique().to_list()
        )
        if len(sources_in_block) < 2:
            return _empty_pair_stream_df() if _emit_dataframe else []

    pairs = find_fuzzy_matches(
        block_df, mk,
        exclude_pairs=exclude_pairs,
        pre_scored_pairs=block.pre_scored_pairs,
        _emit_dataframe=_emit_dataframe,
    )

    if _emit_dataframe:
        # find_fuzzy_matches emits a frame in every branch under the flag.
        assert isinstance(pairs, pl.DataFrame)
        if across_files_only and source_lookup and not pairs.is_empty():
            # Vectorized cross-source filter (W2c: shared seam-routed helper
            # with _score_one_block_columnar -- the old mirror rule is retired).
            pairs = _cross_source_filter_df(pairs, source_lookup)
        return pairs

    # Legacy list path (permanent opt-in alongside the default ``list[tuple]`` path).
    assert isinstance(pairs, list)

    if across_files_only and source_lookup:
        pairs = [
            (a, b, s) for a, b, s in pairs
            if source_lookup.get(a) != source_lookup.get(b)
        ]

    return pairs


_DEFAULT_MAX_WORKERS = 4
"""Default thread-pool size for score_blocks_parallel.

Stays at 4 (NOT cpu_count) because of a memory-pathology observed on
the bench-distributed-stack run 26002766443 against the 5M /
1.67M-block fixture: at max_workers=16 on a 16-core / 64GB runner, RSS
climbed ~3 GB/min through fuzzy_score_blocks and the runner OOM-killed
the job around t=20-76 min. PR #295's same workload with
max_workers=4 finished in 160 min with peak RSS = 4476 MB.

The 14x RSS blow-up isn't parallelism-proportional; it's that each
worker calls block.df.collect() on a LazyFrame which is a FILTER
against the 5M parent df, so 16 simultaneous workers run 16
simultaneous full-table scans whose intermediates accumulate faster
than they're released. Fixing this requires either:

(a) materializing per-block dfs once outside the worker (the
    Component 2 v2 spec direction; turned out to need real
    multi-node infra that we don't have), or
(b) batching tiny blocks into super-blocks so per-worker setup is
    amortized over more rows.

Until (b) ships, keep workers at 4 -- it's the safe default that's
been proven to fit on the bench runner. Callers can override
explicitly via the max_workers kwarg when their workload doesn't
exhibit this pathology."""


def score_blocks_parallel(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    max_workers: int | None = None,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    track_matched: bool = True,
) -> list[tuple[int, int, float]]:
    """Score all blocks in parallel using threads.

    rapidfuzz.cdist releases the GIL, so threads provide real parallelism
    for the expensive fuzzy scoring. Blocks are independent — no shared
    mutable state during scoring.

    Args:
        blocks: List of BlockResult objects.
        mk: Matchkey configuration.
        matched_pairs: Set of already-matched (min_id, max_id) pairs.
        max_workers: Thread pool size. None (default) uses
            ``_DEFAULT_MAX_WORKERS`` (= ``min(cpu_count(), 16)``).
        across_files_only: Filter to cross-source pairs only.
        source_lookup: Row ID to source name mapping.
        target_ids: For match mode — filter to target/ref cross pairs.
        track_matched: when True (default) the per-pass exclude set is
            populated as before. When False the ``matched_pairs.add``
            bookkeeping is skipped -- the caller passes False ONLY when no
            later matchkey pass consumes the set (single-matchkey configs,
            or the last consuming pass). At 1M / 131M pairs that per-pair
            min/max/set.add was ~100s of the default-path wall. The returned
            pair list is identical either way; only the side effect differs.

    Returns:
        All fuzzy pairs found across blocks.
    """
    # Fresh per run: don't inherit a prior dedupe's known-broken NE entries.
    reset_ne_broken()
    if max_workers is None:
        max_workers = _DEFAULT_MAX_WORKERS
    if not blocks:
        return []

    # For small block counts, skip thread overhead
    if len(blocks) <= 2:
        all_pairs = []
        total_candidates = 0
        for block in blocks:
            n = block.n_rows()
            total_candidates += n * (n - 1) // 2
            pairs = _score_one_block(
                block, mk, matched_pairs,
                across_files_only=across_files_only,
                source_lookup=source_lookup,
            )
            if target_ids is not None:
                pairs = [
                    (a, b, s) for a, b, s in pairs
                    if (a in target_ids) != (b in target_ids)
                ]
            all_pairs.extend(pairs)
            if track_matched:
                for a, b, _s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
        _emit_scoring_profile(all_pairs, mk.fuzzy_threshold, candidates_compared=total_candidates)
        return all_pairs

    # Snapshot exclude_pairs so threads see a frozen copy
    frozen_exclude = frozenset(matched_pairs)

    # Total candidate pairs across all blocks -- a single stat that feeds
    # ScoringProfile.candidates_compared. Historical loops materialized
    # every block via `.collect().height` or `.select(pl.len()).collect()`
    # just to read row count. At 1.67M tiny-block workloads (real-shape
    # 5M auto-config on email), BOTH variants caused runner OOM-kills
    # around ~70 min wall: each LazyFrame here is a filter expression
    # over the 5M parent df, and 1.67M `.collect()` calls accumulate
    # Polars arena memory faster than it's released.
    #
    # Bench run history (5M / 1.67M-blocks on large-new-64GB):
    #   PR #295 (.collect().height, max_workers=4): 160 min, 4.5 GB peak. OK.
    #   PR #301 (.select(pl.len()), max_workers=16): RSS climbs to 60+ GB
    #     before scoring starts, OOM-killed. Bumped workers blamed; reverted.
    #   PR #303 (.select(pl.len()), max_workers=4 revert): same OOM.
    #     -> the candidate-count loop ITSELF is the leak at this scale.
    #
    # Cheapest fix: skip the count loop entirely when there are more
    # than _CANDIDATE_COUNT_SKIP_THRESHOLD blocks. The stat becomes 0
    # at scale; profile readers should treat 0 as "skipped at scale,
    # not literally zero candidates." For small-N workloads (the actual
    # use case for the stat -- debugging, diagnostics) we still compute
    # it cheaply because there are few blocks.
    _CANDIDATE_COUNT_SKIP_THRESHOLD = 10_000
    _n_blocks_for_count_gate = len(blocks)
    if _n_blocks_for_count_gate <= _CANDIDATE_COUNT_SKIP_THRESHOLD:
        total_candidates = 0
        for block in blocks:
            try:
                n = block.n_rows()
            except Exception:
                n = 0
            total_candidates += n * (n - 1) // 2
    else:
        # Skip -- the stat isn't load-bearing for scoring correctness.
        logger.info(
            "Skipping candidate-count loop at scale: %d blocks > %d threshold "
            "(ScoringProfile.candidates_compared will be 0)",
            _n_blocks_for_count_gate, _CANDIDATE_COUNT_SKIP_THRESHOLD,
        )
        total_candidates = 0

    all_pairs = []
    total_blocks = len(blocks)
    log_interval = max(total_blocks // 10, 1)  # log ~10 progress updates

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, block in enumerate(blocks):
            future = executor.submit(
                _score_one_block, block, mk, frozen_exclude,
                across_files_only, source_lookup,
            )
            future_to_idx[future] = i

        completed = 0
        for future in as_completed(future_to_idx):
            pairs = future.result()
            if target_ids is not None:
                pairs = [
                    (a, b, s) for a, b, s in pairs
                    if (a in target_ids) != (b in target_ids)
                ]
            all_pairs.extend(pairs)
            if track_matched:
                for a, b, _s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
            completed += 1
            if completed % log_interval == 0:
                logger.info(
                    "Scoring progress: %d/%d blocks (%d%%), %d pairs so far",
                    completed, total_blocks,
                    int(completed / total_blocks * 100),
                    len(all_pairs),
                )

    logger.info(
        "Parallel scoring: %d blocks, %d workers, %d pairs found",
        total_blocks, max_workers, len(all_pairs),
    )
    _emit_scoring_profile(all_pairs, mk.fuzzy_threshold, candidates_compared=total_candidates)
    return all_pairs


# ---------------------------------------------------------------------------
# Cross-encoder reranking
# ---------------------------------------------------------------------------

def rerank_top_pairs(
    pairs: list[tuple[int, int, float]],
    df: Any,  # pl.DataFrame | pa.Table (A8: seam reads)
    mk: MatchkeyConfig,
) -> list[tuple[int, int, float]]:
    """Re-score borderline pairs with a pre-trained cross-encoder.

    Pairs within a band around the threshold (threshold +/- rerank_band)
    are re-scored using a cross-encoder model. Pairs outside the band
    keep their original scores. No training needed -- uses an off-the-shelf
    cross-encoder for zero-shot reranking.

    Args:
        pairs: All scored pairs (row_id_a, row_id_b, score).
        df: Full collected DataFrame with record data.
        mk: Matchkey config with rerank, rerank_model, rerank_band, threshold.

    Returns:
        Updated pairs list with reranked scores for borderline pairs.
    """
    if not mk.rerank or not pairs or mk.threshold is None:
        return pairs

    try:
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]  # optional dep, ImportError caught below
            CrossEncoder,
        )
    except ImportError:
        logger.warning("Cross-encoder reranking unavailable: sentence-transformers not installed")
        return pairs

    band = mk.rerank_band
    lo = mk.threshold - band
    hi = mk.threshold + band

    # Identify borderline pairs
    borderline_idx = [i for i, (_, _, s) in enumerate(pairs) if lo <= s <= hi]
    if not borderline_idx:
        logger.info("Rerank: no pairs in band [%.2f, %.2f], skipping", lo, hi)
        return pairs

    # Build row lookup for serialization
    from goldenmatch.core.frame import to_frame as _tf_a8

    _fa8 = _tf_a8(df)
    matchable_cols = [c for c in _fa8.columns if not c.startswith("__")]
    row_lookup: dict[int, dict] = {}
    for row in _fa8.select_dicts(["__row_id__"] + matchable_cols):
        row_lookup[row["__row_id__"]] = row

    # Serialize borderline pairs
    from goldenmatch.core.cross_encoder import serialize_record

    sentence_pairs = []
    for idx in borderline_idx:
        a, b, _ = pairs[idx]
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        text_a = serialize_record(row_a, matchable_cols)
        text_b = serialize_record(row_b, matchable_cols)
        sentence_pairs.append((text_a, text_b))

    # Score with cross-encoder
    logger.info(
        "Rerank: scoring %d borderline pairs with %s",
        len(borderline_idx), mk.rerank_model,
    )
    model = CrossEncoder(mk.rerank_model)
    from goldenmatch.core.cross_encoder import score_pairs as ce_score_pairs

    ce_scores = ce_score_pairs(model, sentence_pairs)

    # Replace scores for borderline pairs
    result = list(pairs)
    for i, idx in enumerate(borderline_idx):
        a, b, _ = result[idx]
        result[idx] = (a, b, float(ce_scores[i]))

    # Re-filter by threshold
    result = [(a, b, s) for a, b, s in result if s >= mk.threshold]

    logger.info(
        "Rerank: %d pairs after reranking (was %d)",
        len(result), len(pairs),
    )
    return result


# ---------------------------------------------------------------------------
# Arrow-native roadmap Phase 1a (#623): columnar pair-stream entry points
# ---------------------------------------------------------------------------
#
# Sibling functions to the list-returning scorers above. Same inputs, same
# scoring math, but return ``pl.DataFrame`` instead of ``list[tuple]``. Let
# Phase 1b callers (build_clusters, web preview, lineage, identity edge
# ingestion, MCP/REST surfaces) migrate piecewise without breaking any
# existing list-based consumer.
#
# Phase 1c will invert the relationship: the columnar functions become the
# canonical implementation and the list versions become thin
# ``.to_pairs_list()`` shims, eventually removed entirely.
#
# Today's implementation is a wrap-and-convert: the list path runs, then we
# build the DataFrame from the result. That carries Phase 1a's correctness
# guarantee (the inner scoring is byte-identical) at the cost of a single
# Python -> Arrow conversion per call. The conversion is O(N_pairs); at the
# 200M-pair / 5M-row reference shape that's ~10s of overhead, recovered in
# Phase 1c. Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
# (gitignored).

_PAIR_STREAM_SCHEMA_CACHE: dict[str, pl.DataType] | None = None


def _pair_stream_schema() -> dict[str, pl.DataType]:
    """Canonical pair-stream schema. ``id_a < id_b`` invariant maintained by the
    caller (legacy scorers already canonicalize via ``(min, max)``).

    Built lazily -- not a module-level ``pl.`` literal -- so importing this
    module doesn't import Polars eagerly (W0 polars-eviction gate). Cached
    after the first call. The public ``PAIR_STREAM_SCHEMA`` name is still
    importable (module ``__getattr__`` below) for external consumers (tests,
    ``cluster.py``, ``pairs.py``) that expect a plain dict attribute.
    """
    global _PAIR_STREAM_SCHEMA_CACHE
    if _PAIR_STREAM_SCHEMA_CACHE is None:
        # Derived from the backend-neutral spec (W2c): frame.py's
        # PAIR_STREAM_SCHEMA_SPEC is the single source of truth; this maps it
        # to Polars dtypes for the Polars-typed consumers.
        from goldenmatch.core.frame import PAIR_STREAM_SCHEMA_SPEC

        _pl_dtype = {"int64": pl.Int64, "float64": pl.Float64, "utf8": pl.Utf8, "bool": pl.Boolean, "uint32": pl.UInt32}
        _PAIR_STREAM_SCHEMA_CACHE = {
            k: _pl_dtype[v]() for k, v in PAIR_STREAM_SCHEMA_SPEC.items()
        }
    return _PAIR_STREAM_SCHEMA_CACHE


def __getattr__(name: str) -> dict[str, pl.DataType]:
    """PEP 562 module attribute hook: resolves ``PAIR_STREAM_SCHEMA`` lazily
    for ``from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA`` / attribute
    access, without executing ``pl.Int64()`` etc. at module import time. Only
    fires for names not already bound in the module's ``__dict__`` (i.e. not
    for bare-name references inside this module's own functions, which call
    ``_pair_stream_schema()`` directly above)."""
    if name == "PAIR_STREAM_SCHEMA":
        return _pair_stream_schema()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _empty_pair_stream_df() -> pl.DataFrame:
    """Zero-row canonical pair-stream frame, via the seam (W2c)."""
    from goldenmatch.core.frame import PAIR_STREAM_SCHEMA_SPEC, empty_frame

    return empty_frame(PAIR_STREAM_SCHEMA_SPEC, backend="polars").native


def _concat_pair_frames(frames: list[pl.DataFrame]) -> pl.DataFrame:
    """Vertical concat of pair-stream frames, via the seam (W2c)."""
    from goldenmatch.core.frame import concat_frames, to_frame

    return concat_frames([to_frame(f) for f in frames]).native


def pairs_list_to_df(pairs: list[tuple[int, int, float]]) -> pl.DataFrame:
    """Adapter: legacy ``(id_a, id_b, score)`` list -> typed DataFrame.

    Empty input returns a zero-row frame with the canonical schema so
    downstream Polars expressions (joins, group_by, with_columns) work
    without an ``if df.is_empty()`` guard at every call site.
    """
    # W2c: routed through the seam's row constructor (explicit spec schema;
    # backend pinned to polars until W2d threads Frames between stages).
    from goldenmatch.core.frame import PAIR_STREAM_SCHEMA_SPEC, frame_from_rows

    return frame_from_rows(pairs, PAIR_STREAM_SCHEMA_SPEC, backend="polars").native


def pairs_df_to_list(df: pl.DataFrame) -> list[tuple[int, int, float]]:
    """Adapter: DataFrame pair stream -> legacy list shape.

    Live dependency of the columnar pipeline's scored_pairs capture (Phase 2 SP3,
    `core/pipeline.py`), in addition to migrating call sites needing the list shape.
    """
    if df.is_empty():
        return []
    return [
        (int(a), int(b), float(s))
        for a, b, s in zip(
            df["id_a"].to_list(),
            df["id_b"].to_list(),
            df["score"].to_list(),
            strict=True,
        )
    ]


def find_fuzzy_matches_columnar(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
    pre_scored_pairs: list[tuple[int, int, float]] | None = None,
) -> pl.DataFrame:
    """Columnar wrapper around :func:`find_fuzzy_matches`.

    Returns a typed Polars DataFrame ``(id_a, id_b, score)`` with the
    ``PAIR_STREAM_SCHEMA`` shape. Behavior identical to the list version
    (same scoring math, same canonicalization, same threshold filter);
    only the return shape differs.

    Phase 1c hot-path optimization (#623): when the call hits the hot
    path (no NE, no exclude_pairs, no pre_scored_pairs), we pass
    ``_emit_dataframe=True`` so ``find_fuzzy_matches`` emits the
    DataFrame directly from its numpy arrays — bypassing the
    list-of-tuples construction that dominates the wall at 200M-pair
    scale. Non-hot-path branches still wrap-and-convert (rare in
    production: NE is opt-in, exclude_pairs is empty for first-pass
    blocking).
    """
    is_hot_path = (
        not mk.negative_evidence
        and (exclude_pairs is None or len(exclude_pairs) == 0)
        and pre_scored_pairs is None
    )
    if is_hot_path:
        result = find_fuzzy_matches(
            block_df, mk, exclude_pairs, pre_scored_pairs,
            _emit_dataframe=True,
        )
        # Post Phase-1 Wave 1, find_fuzzy_matches honours _emit_dataframe on
        # ALL branches (incl. the n<2 / total_weight==0 early returns), so this
        # is always a DataFrame. The isinstance wrap is a belt-and-suspenders
        # no-op kept against any future branch that forgets the flag.
        if isinstance(result, pl.DataFrame):
            return result
        return pairs_list_to_df(result)
    pairs = find_fuzzy_matches(block_df, mk, exclude_pairs, pre_scored_pairs)
    assert isinstance(pairs, list)
    return pairs_list_to_df(pairs)


def _score_one_block_columnar(
    block: Any,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
) -> pl.DataFrame:
    """Columnar twin of :func:`_score_one_block`. Returns a Polars
    DataFrame with ``PAIR_STREAM_SCHEMA`` shape via
    ``find_fuzzy_matches_columnar``'s hot-path direct emit
    (``_emit_dataframe=True``).

    Skips the list-of-tuples accumulation that ``_score_one_block``
    pays before its caller would re-convert. Across-files filtering is
    applied as a Polars expression on the result frame, vectorized.

    NOTE (W2c): the across-files filter is shared with
    ``_score_one_block(..., _emit_dataframe=True)`` via
    ``_cross_source_filter_df`` -- the old mirror rule is retired.
    """
    block_df = block.materialize().native

    if across_files_only and source_lookup:
        from goldenmatch.core.frame import to_frame as _tf_ps

        sources_in_block = _tf_ps(block_df).column("__source__").unique().to_list()
        if len(sources_in_block) < 2:
            return _empty_pair_stream_df()

    pairs_df = find_fuzzy_matches_columnar(
        block_df, mk,
        exclude_pairs=exclude_pairs,
        pre_scored_pairs=block.pre_scored_pairs,
    )

    if across_files_only and source_lookup and not pairs_df.is_empty():
        # Vectorized cross-source filter (W2c: shared seam-routed helper with
        # _score_one_block's _emit_dataframe branch -- mirror rule retired).
        pairs_df = _cross_source_filter_df(pairs_df, source_lookup)

    return pairs_df


def score_blocks_columnar(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    max_workers: int | None = None,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    track_matched: bool = True,
) -> pl.DataFrame:
    """Phase 1c-real columnar block scorer. Mirrors
    :func:`score_blocks_parallel`'s thread-pool structure but uses
    ``find_fuzzy_matches_columnar`` (with ``_emit_dataframe=True`` on
    the hot path) at each leaf and ``pl.concat`` at aggregation -- no
    list-of-tuples intermediate.

    The 2026-05-31 bench (run 26716412152) showed that the previous
    wrap-and-convert implementation -- calling ``score_blocks_parallel``
    then ``pairs_list_to_df`` at the boundary -- was 13% slower at
    100K and 25% slower at 1M than the list path, because the
    inner-loop optimization in ``find_fuzzy_matches`` never
    propagated up. This rewrite makes the columnar path actually
    columnar end-to-end.

    Args:
        blocks, mk, matched_pairs, max_workers, across_files_only,
        source_lookup, target_ids: same semantics as
        ``score_blocks_parallel``. ``matched_pairs`` is mutated in
        place as the contract requires.
        track_matched: when True (default) the per-pass exclude set is
            populated as before. When False the ``matched_pairs.add``
            bookkeeping is skipped entirely -- the pipeline's columnar
            path is single-matchkey by eligibility, so no later pass
            ever consumes the set and building it is pure waste (the
            profiled ~104s at 1M / 131M pairs). The returned pair stream
            is identical either way; only the side effect differs.

    Returns:
        Polars DataFrame with ``PAIR_STREAM_SCHEMA`` shape.
    """
    # Fresh per run: don't inherit a prior dedupe's known-broken NE entries.
    reset_ne_broken()
    if max_workers is None:
        max_workers = _DEFAULT_MAX_WORKERS
    if not blocks:
        return _empty_pair_stream_df()

    # Small block count: skip thread overhead (mirrors
    # score_blocks_parallel's <=2 branch).
    if len(blocks) <= 2:
        frames: list[pl.DataFrame] = []
        for block in blocks:
            df_pairs = _score_one_block_columnar(
                block, mk, matched_pairs,
                across_files_only=across_files_only,
                source_lookup=source_lookup,
            )
            if target_ids is not None and not df_pairs.is_empty():
                df_pairs = _filter_target_ids_df(df_pairs, target_ids)
            if not df_pairs.is_empty():
                # Update matched_pairs side effect (per-block, before
                # concat so order is consistent with the list path).
                # Skipped when track_matched=False (set is never consumed).
                if track_matched:
                    for a, b in zip(
                        df_pairs["id_a"].to_list(),
                        df_pairs["id_b"].to_list(),
                        strict=True,
                    ):
                        matched_pairs.add((min(a, b), max(a, b)))
                frames.append(df_pairs)
        if not frames:
            return _empty_pair_stream_df()
        return _concat_pair_frames(frames)

    # Parallel path: ThreadPoolExecutor, mirroring score_blocks_parallel.
    # rapidfuzz.cdist + the native scorer release the GIL on the hot
    # path, so threads give real parallelism.
    frozen_exclude = frozenset(matched_pairs)
    frames = []
    total_blocks = len(blocks)
    log_interval = max(total_blocks // 10, 1)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, block in enumerate(blocks):
            future = executor.submit(
                _score_one_block_columnar, block, mk, frozen_exclude,
                across_files_only, source_lookup,
            )
            future_to_idx[future] = i

        completed = 0
        for future in as_completed(future_to_idx):
            df_pairs = future.result()
            if target_ids is not None and not df_pairs.is_empty():
                df_pairs = _filter_target_ids_df(df_pairs, target_ids)
            if not df_pairs.is_empty():
                if track_matched:
                    for a, b in zip(
                        df_pairs["id_a"].to_list(),
                        df_pairs["id_b"].to_list(),
                        strict=True,
                    ):
                        matched_pairs.add((min(a, b), max(a, b)))
                frames.append(df_pairs)
            completed += 1
            if completed % log_interval == 0:
                # Match the list path's log line shape (without the
                # exact pair count since aggregation is deferred).
                logger.info(
                    "Scoring progress (columnar): %d/%d blocks (%d%%)",
                    completed, total_blocks,
                    int(completed / total_blocks * 100),
                )

    if not frames:
        return _empty_pair_stream_df()
    return _concat_pair_frames(frames)


def _filter_target_ids_df(
    pairs_df: pl.DataFrame, target_ids: set[int],
) -> pl.DataFrame:
    """Vectorized equivalent of the list-path's per-pair
    ``(a in target_ids) != (b in target_ids)`` filter. Match mode
    keeps only pairs where exactly one of (id_a, id_b) is in
    ``target_ids``. W2c: routed through the seam's filter_target_split."""
    if pairs_df.is_empty():
        return pairs_df
    from goldenmatch.core.frame import to_frame

    return to_frame(pairs_df).filter_target_split("id_a", "id_b", list(target_ids)).native


def _cross_source_filter_df(pairs_df: pl.DataFrame, source_lookup: dict[int, str]) -> pl.DataFrame:
    """The across-files cross-source filter, ONE implementation for both
    ``_score_one_block(_emit_dataframe=True)`` and
    ``_score_one_block_columnar`` (they were byte-identical mirror-flagged
    twins before W2c; sharing the body retires the mirror rule). Join
    row_id -> source onto both endpoints, keep pairs whose sources differ,
    drop the helper columns. Null-source rows DROP (columnar-engine
    semantics; unreachable in-pipeline because source_lookup is total)."""
    from goldenmatch.core.frame import ArrowFrame, frame_from_columns, to_frame

    pairs_frame = to_frame(pairs_df)
    src_map = frame_from_columns(
        {
            "__row_id__": list(source_lookup.keys()),
            "__src__": list(source_lookup.values()),
        },
        {"__row_id__": "int64", "__src__": "utf8"},
        backend="arrow" if isinstance(pairs_frame, ArrowFrame) else "polars",
    )
    return (
        pairs_frame
        .join_left(src_map.rename({"__row_id__": "id_a", "__src__": "src_a"}), on="id_a")
        .join_left(src_map.rename({"__row_id__": "id_b", "__src__": "src_b"}), on="id_b")
        .filter_ne_cols("src_a", "src_b")
        .drop(["src_a", "src_b"])
        .native
    )
