"""Reference data — bundled OSS lookups that lift engine accuracy.

Strategy direction #8 (`docs/superpowers/specs/2026-05-08-competitive-strategy-review.md`):
close the Senzing/LexisNexis reference-data moat on the 80% case with bundled
public-domain / permissively-licensed datasets.

This first slice ships the **reference-people** pack: a 2010 U.S. Census
top-10K surname frequency table plus a frequency-weighted Jaro–Winkler scorer
(``name_freq_weighted_jw``). The scorer down-weights matches on common
surnames (Smith, Johnson) and up-weights matches on rare ones, on top of the
underlying Jaro–Winkler edit similarity.

Usage:

    import goldenmatch.refdata  # registers the scorer

    # then in YAML or MatchkeyField config:
    #   scorer: name_freq_weighted_jw

The scorer is registered into ``PluginRegistry`` at import time. The data file
is bundled in the wheel; ``surname_frequency`` and ``surname_idf`` return
``None`` if the file is missing (e.g. exotic build layout) rather than raising.

Provenance + license for every bundled dataset:
``goldenmatch/refdata/data/PROVENANCE.md``.
"""
from __future__ import annotations

from goldenmatch.refdata.scorer import register_scorers
from goldenmatch.refdata.surnames import (
    is_available,
    surname_count,
    surname_frequency,
    surname_idf,
    surname_rank,
)

# Register the bundled scorers on import. Idempotent — register_scorers checks
# the registry before re-registering.
register_scorers()

__all__ = [
    "is_available",
    "surname_count",
    "surname_frequency",
    "surname_idf",
    "surname_rank",
]
