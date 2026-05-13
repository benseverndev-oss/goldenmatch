"""US Census 2010 top-10K surname frequency lookup.

The table covers roughly 90% of the U.S. population by count. Names not
in the table fall back to a "rare" weight (treated as if they had a
count equal to the minimum observed count). Lookup is case-insensitive;
non-alphabetic characters are stripped before lookup.

Public API:

- ``surname_count(name)`` — raw count from the table, or ``None`` if not found.
- ``surname_rank(name)`` — 1-indexed rank, or ``None`` if not found.
- ``surname_frequency(name)`` — share of population covered by the bundle, in [0, 1].
- ``surname_idf(name)`` — IDF-style weight in [0, 1]; rare ~= 1.0, common ~= 0.0.
- ``is_available()`` — True iff the bundled data file was found at import time.
"""
from __future__ import annotations

import csv
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from threading import Lock

logger = logging.getLogger(__name__)

_DATA_FILE = "census_surnames_2010_top10k.csv"


@dataclass(frozen=True)
class _SurnameState:
    """Loaded state. Frozen so readers see a consistent snapshot; reload
    via ``_reload`` swaps the whole object atomically (no in-place dict
    mutation that could race with concurrent lookups)."""

    counts: Mapping[str, int]
    ranks: Mapping[str, int]
    total_count: int
    min_count: int  # smallest observed count; pinning the IDF denominator


_lock = Lock()
_state: _SurnameState | None = None


def _normalize(name: str) -> str:
    return "".join(ch for ch in name if ch.isalpha()).upper()


def _build_state_from_file() -> _SurnameState | None:
    counts: dict[str, int] = {}
    ranks: dict[str, int] = {}
    total = 0
    min_count = 0
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
    if not counts:
        return None
    return _SurnameState(
        counts=counts, ranks=ranks, total_count=total, min_count=min_count or 1,
    )


def _load() -> None:
    global _state
    if _state is not None:
        return
    with _lock:
        if _state is not None:
            return
        try:
            _state = _build_state_from_file()
        except (FileNotFoundError, KeyError, ValueError) as exc:
            logger.warning(
                "goldenmatch.refdata.surnames: data file unavailable (%s); "
                "lookups will return None.",
                exc,
            )
            _state = None


def _reload() -> None:
    """Test-only: drop the cached state; next access re-parses."""
    global _state
    with _lock:
        _state = None


def is_available() -> bool:
    _load()
    return _state is not None


def surname_count(name: str | None) -> int | None:
    if name is None:
        return None
    _load()
    if _state is None:
        return None
    return _state.counts.get(_normalize(name))


def surname_rank(name: str | None) -> int | None:
    if name is None:
        return None
    _load()
    if _state is None:
        return None
    return _state.ranks.get(_normalize(name))


def surname_frequency(name: str | None) -> float | None:
    """Share of bundled-table population covered by ``name``, in [0, 1].

    Returns ``None`` for unknown names. Unknown != zero — a name absent
    from the top-10K table is rarer than any name in it, and callers
    should typically treat unknown as "use the rare-name weight". See
    ``surname_idf`` for the weighted variant.
    """
    c = surname_count(name)
    if c is None:
        return None
    _load()
    if _state is None or _state.total_count <= 0:
        return None
    return c / _state.total_count


def surname_idf(name: str | None) -> float | None:
    """IDF-style weight in [0, 1] for the named surname.

    - Rare names (count == min observed) → weight near 1.0.
    - Common names (Smith, Johnson) → weight near 0.0.
    - Unknown names (not in top-10K) → 1.0 (treated as rarer than observed).
    - ``None`` input → ``None``.

    Formula: ``idf = log(total / count) / log(total / min_count)``.
    """
    if name is None:
        return None
    _load()
    if _state is None or _state.total_count <= 0 or _state.min_count <= 0:
        return None
    c = _state.counts.get(_normalize(name))
    if c is None:
        return 1.0  # OOV: rarer than anything in the table
    if c >= _state.total_count:
        return 0.0
    numerator = math.log(_state.total_count / c)
    denominator = math.log(_state.total_count / _state.min_count)
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))
