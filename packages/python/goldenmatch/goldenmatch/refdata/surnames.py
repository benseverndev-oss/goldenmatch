"""US Census 2010 top-10K surname frequency lookup.

The table covers roughly 90% of the U.S. population by count. Names not in
the table fall back to a `rare` weight (treated as if they had a count
equal to the minimum observed count, so they get the strongest match
bonus). Lookup is case-insensitive; non-alphabetic characters in the input
are stripped before lookup.

Public API:

- ``surname_count(name)`` — raw count from the table, or ``None`` if not found.
- ``surname_rank(name)`` — 1-indexed rank, or ``None`` if not found.
- ``surname_frequency(name)`` — share of population, in [0, 1].
- ``surname_idf(name)`` — IDF-style weight in [0, 1]; rare = ~1.0, common = ~0.0.
- ``is_available()`` — True iff the bundled data file was found at import time.

Loading is lazy: the CSV is parsed on the first lookup and cached. Reload by
calling ``_reload()`` (test-only).
"""
from __future__ import annotations

import csv
import logging
import math
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "census_surnames_2010_top10k.csv"

# Total U.S. population used to compute frequency. Per Census 2010, the
# `cum_prop100k` of the last row in the source file is 90063.03, meaning
# ~90.063% of population is covered by the top-162K names. The top-10K bundle
# we ship covers ~80%+ of population. We treat the *total* population for
# frequency as 100k (matches the source's `prop100k` semantics) so callers
# can compare frequencies on a familiar scale.
_PER_100K_DENOM = 100_000.0

# Sum of counts across all rows = "population represented by the top-10K".
# Computed once on load; stored in ``_state``.

_lock = Lock()
_state: dict = {
    "loaded": False,
    "available": False,
    "counts": {},  # name (upper-cased, alpha-only) -> count
    "ranks": {},
    "total_count": 0,
    "min_count": 0,
}


def _normalize(name: str) -> str:
    """Upper-case and strip non-alpha characters. Empty string is a valid input."""
    return "".join(ch for ch in name if ch.isalpha()).upper()


def _load() -> None:
    if _state["loaded"]:
        return
    with _lock:
        if _state["loaded"]:
            return
        counts: dict[str, int] = {}
        ranks: dict[str, int] = {}
        total = 0
        min_count = 0
        try:
            with resources.files("goldenmatch.refdata.data").joinpath(_DATA_FILE).open(
                "r", encoding="utf-8"
            ) as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    name = _normalize(row["name"])
                    if not name:
                        continue
                    count = int(row["count"])
                    rank = int(row["rank"])
                    counts[name] = count
                    ranks[name] = rank
                    total += count
                    if min_count == 0 or count < min_count:
                        min_count = count
            _state["available"] = bool(counts)
            _state["counts"] = counts
            _state["ranks"] = ranks
            _state["total_count"] = total
            _state["min_count"] = min_count or 1
        except (FileNotFoundError, ModuleNotFoundError, KeyError, ValueError) as exc:
            # Missing data file or malformed row: degrade gracefully.
            logger.warning(
                "goldenmatch.refdata.surnames: data file unavailable (%s); "
                "lookups will return None.",
                exc,
            )
            _state["available"] = False
        finally:
            _state["loaded"] = True


def _reload() -> None:
    """Test-only: force re-parse of the data file."""
    with _lock:
        _state["loaded"] = False
        _state["available"] = False
        _state["counts"] = {}
        _state["ranks"] = {}
        _state["total_count"] = 0
        _state["min_count"] = 0
    _load()


def is_available() -> bool:
    """True iff the bundled surname data was found and parsed."""
    _load()
    return _state["available"]


def surname_count(name: str | None) -> int | None:
    """Raw Census 2010 count for ``name``, or ``None`` if not in the top 10K."""
    if name is None:
        return None
    _load()
    if not _state["available"]:
        return None
    return _state["counts"].get(_normalize(name))


def surname_rank(name: str | None) -> int | None:
    """1-indexed Census 2010 rank for ``name``, or ``None`` if not in the top 10K."""
    if name is None:
        return None
    _load()
    if not _state["available"]:
        return None
    return _state["ranks"].get(_normalize(name))


def surname_frequency(name: str | None) -> float | None:
    """Share of population covered by the bundled table, in [0, 1].

    Returns ``None`` for unknown names. Unknown is **not** the same as zero —
    a name absent from the top-10K table is rarer than any name in it, and
    callers should typically treat unknown as "use the rare-name weight".
    See ``surname_idf`` for the weighted variant that handles this directly.
    """
    c = surname_count(name)
    if c is None:
        return None
    _load()
    total = _state["total_count"]
    if total <= 0:
        return None
    return c / total


def surname_idf(name: str | None) -> float | None:
    """IDF-style weight in [0, 1] for the named surname.

    - Rare names (count == min observed) → weight near 1.0.
    - Common names (Smith, Johnson) → weight near 0.0.
    - Unknown names (not in top-10K) → 1.0 (treated as rarer than anything observed).
    - ``None`` input → ``None``.

    Formula::

        idf(name) = log(total / count) / log(total / min_count)

    Bounded to [0, 1] via the denominator (the rarest known surname yields 1.0).
    """
    if name is None:
        return None
    _load()
    if not _state["available"]:
        return None
    total = _state["total_count"]
    min_c = _state["min_count"]
    if total <= 0 or min_c <= 0:
        return None
    c = _state["counts"].get(_normalize(name))
    if c is None:
        # Out-of-vocabulary: rarer than anything we've seen.
        return 1.0
    if c >= total:
        return 0.0
    numerator = math.log(total / c)
    denominator = math.log(total / min_c)
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))
