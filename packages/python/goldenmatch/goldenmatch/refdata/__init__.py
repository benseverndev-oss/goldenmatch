"""Reference data — bundled OSS lookups that lift engine accuracy.

Strategy direction #8 (`docs/superpowers/specs/2026-05-08-competitive-strategy-review.md`):
close the Senzing/LexisNexis reference-data moat on the 80% case with bundled
public-domain / permissively-licensed datasets.

The **reference-people** pack ships two lookups and two scorers:

- **Surnames** — US Census 2010 top-10K frequency table plus
  ``name_freq_weighted_jw`` scorer.
- **Given-name aliases** — curated canonical → nickname table plus
  ``given_name_aliased_jw`` scorer.

The **reference-business** pack ships one transform:

- **Legal-form normalization** — strips trailing corporate suffixes
  (``legal_form_strip``).
"""
from __future__ import annotations

from goldenmatch.refdata.business import is_available as business_available
from goldenmatch.refdata.business import (
    known_variants as legal_form_variants,
)
from goldenmatch.refdata.business import register_transforms, strip_legal_form
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

# Register on import. Idempotent.
register_scorers()
register_transforms()

__all__ = [
    "aliases_of",
    "are_equivalent",
    "business_available",
    "canonical_form",
    "given_names_available",
    "is_available",
    "legal_form_variants",
    "strip_legal_form",
    "surname_count",
    "surname_frequency",
    "surname_idf",
    "surname_rank",
]
