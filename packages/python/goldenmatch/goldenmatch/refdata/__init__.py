"""Reference data — bundled OSS lookups that lift engine accuracy.

Strategy direction #8 (`docs/superpowers/specs/2026-05-08-competitive-strategy-review.md`):
close the Senzing/LexisNexis reference-data moat on the 80% case with bundled
public-domain / permissively-licensed datasets.

The **reference-people** pack ships two lookups and two scorers:

- **Surnames** — US Census 2010 top-10K frequency table plus
  ``name_freq_weighted_jw`` scorer (down-weights common-surname matches in
  the borderline JW zone). Built for ``last_name`` fields.
- **Given-name aliases** — curated canonical → nickname table (Robert ↔ Bob,
  William ↔ Bill, …) plus ``given_name_aliased_jw`` scorer (promotes
  known-alias pairs to 1.0 regardless of edit distance). Built for
  ``first_name`` fields.

Usage:

    import goldenmatch.refdata  # registers both scorers

    # then in YAML or MatchkeyField config:
    #   - field: last_name
    #     scorer: name_freq_weighted_jw
    #   - field: first_name
    #     scorer: given_name_aliased_jw

Both scorers are registered into ``PluginRegistry`` at import time. Data
files are bundled in the wheel; lookups return ``None`` / empty / plain-JW
fallback if a file is missing rather than raising.

Provenance + license for every bundled dataset:
``goldenmatch/refdata/data/PROVENANCE.md``.
"""
from __future__ import annotations

from goldenmatch.refdata.given_names import (
    aliases_of,
    are_equivalent,
    canonical_form,
)
from goldenmatch.refdata.given_names import is_available as given_names_available
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
    "aliases_of",
    "are_equivalent",
    "canonical_form",
    "given_names_available",
    "is_available",
    "surname_count",
    "surname_frequency",
    "surname_idf",
    "surname_rank",
]
