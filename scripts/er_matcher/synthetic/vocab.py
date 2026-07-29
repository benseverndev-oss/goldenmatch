"""Census-weighted name/address vocabulary sampler.

Pure stdlib. Reads bundled data files under ``data/`` (resolved relative to
this file, not the process CWD) exactly once per :class:`Vocab` instance.
Every random draw takes an injected ``random.Random`` so callers control
determinism per-seed.
"""

from __future__ import annotations

import bisect
import csv
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

_STREET_NAMES = (
    "Main",
    "Oak",
    "Pine",
    "Maple",
    "Cedar",
    "Elm",
    "Washington",
    "Lake",
    "Hill",
    "Park",
    "Sunset",
    "Highland",
    "Church",
    "Spring",
    "River",
    "Ridge",
    "Meadow",
    "Forest",
    "Union",
    "Franklin",
)

_STREET_SUFFIXES = ("St", "Ave", "Blvd", "Dr", "Ln", "Rd", "Ct", "Way")


class Vocab:
    """Frequency-weighted census surname sampler + uniform first-name/address sampler."""

    def __init__(self) -> None:
        surnames, ranks, cum_weights = _load_surnames(
            os.path.join(_DATA_DIR, "census_surnames.csv")
        )
        self._surnames = surnames
        self._ranks = ranks
        self._cum_weights = cum_weights
        self._total_weight = cum_weights[-1] if cum_weights else 0

        self._first_names = _load_first_names(os.path.join(_DATA_DIR, "first_names.txt"))

        self._cities = _load_cities(os.path.join(_DATA_DIR, "cities.csv"))

    @property
    def n_surnames(self) -> int:
        return len(self._surnames)

    @property
    def n_first_names(self) -> int:
        return len(self._first_names)

    def sample_surname(self, rng) -> str:
        """Frequency-weighted draw over the census surname list. O(log n) via bisect."""
        draw = rng.random() * self._total_weight
        idx = bisect.bisect_right(self._cum_weights, draw)
        idx = min(idx, len(self._surnames) - 1)
        return _capitalize_surname(self._surnames[idx])

    def sample_first_name(self, rng) -> str:
        """Uniform draw over the bundled given-name list (lean: no frequency weighting)."""
        return rng.choice(self._first_names)

    def sample_person_name(self, rng) -> tuple[str, str]:
        return self.sample_first_name(rng), self.sample_surname(rng)

    def surname_rank(self, name_upper: str) -> int:
        """Rank (1 = most common) for an upper-cased surname. Raises KeyError if unknown."""
        return self._ranks[name_upper]

    def sample_address(self, rng) -> dict:
        city, state, zip_prefix = rng.choice(self._cities)
        number = rng.randint(1, 9999)
        street_name = rng.choice(_STREET_NAMES)
        suffix = rng.choice(_STREET_SUFFIXES)
        zip_suffix = f"{rng.randint(0, 99):02d}"
        return {
            "street": f"{number} {street_name} {suffix}",
            "city": city,
            "state": state,
            "zip": f"{zip_prefix}{zip_suffix}",
        }


def _capitalize_surname(name: str) -> str:
    """Title-case a surname, re-capitalizing the letter after a leading Mc/Mac.

    ``str.title()`` alone turns ``MCDONALD`` into ``Mcdonald`` and
    ``MACARTHUR`` into ``Macarthur``. Mc/Mac-prefixed names are ~2.6% of the
    bundled census surnames, so this matters for a name-matching vocab.
    """
    titled = name.title()
    lower = name.lower()
    if lower.startswith("mc") and len(titled) > 2:
        return titled[:2] + titled[2].upper() + titled[3:]
    if lower.startswith("mac") and len(titled) > 3:
        return titled[:3] + titled[3].upper() + titled[4:]
    return titled


def _load_surnames(path: str) -> tuple[list[str], dict[str, int], list[int]]:
    names: list[str] = []
    ranks: dict[str, int] = {}
    cum_weights: list[int] = []
    running = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"]
            names.append(name)
            ranks[name] = int(row["rank"])
            running += int(row["count"])
            cum_weights.append(running)
    return names, ranks, cum_weights


def _load_first_names(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _load_cities(path: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["city"], row["state"], row["zip_prefix"]))
    return rows
