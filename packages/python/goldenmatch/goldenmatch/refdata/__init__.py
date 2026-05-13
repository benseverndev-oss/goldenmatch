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

The **reference-business** pack ships one transform:

- **Legal-form normalization** — strips trailing corporate suffixes
  (Inc, LLC, GmbH, Pty Ltd, …) so "Acme Inc." and "Acme Incorporated"
  collapse to "Acme" before scoring. Use the ``legal_form_strip``
  transform name in a matchkey's ``transforms:`` list.

The **reference-address** pack ships one transform:

- **USPS-style address normalization** — collapses street-suffix,
  directional, and secondary-unit variants to USPS Publication 28
  canonical short forms ("Street" → "st", "North" → "n", "Apartment"
  → "apt") so "123 Main Street North" and "123 Main St N" reduce
  to "123 main st n" before scoring. Use the ``address_normalize``
  transform name.

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

from goldenmatch.refdata.addresses import is_available as addresses_available
from goldenmatch.refdata.addresses import (
    known_tokens as address_tokens,
)
from goldenmatch.refdata.addresses import normalize_address
from goldenmatch.refdata.addresses import register_transforms as _register_address_transforms
from goldenmatch.refdata.business import is_available as business_available
from goldenmatch.refdata.business import (
    known_variants as legal_form_variants,
)
from goldenmatch.refdata.business import register_transforms as _register_business_transforms
from goldenmatch.refdata.business import strip_legal_form
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

# Register the bundled scorers + transforms on import. Idempotent.
register_scorers()
_register_business_transforms()
_register_address_transforms()

__all__ = [
    "address_tokens",
    "addresses_available",
    "aliases_of",
    "are_equivalent",
    "business_available",
    "canonical_form",
    "given_names_available",
    "is_available",
    "legal_form_variants",
    "normalize_address",
    "strip_legal_form",
    "surname_count",
    "surname_frequency",
    "surname_idf",
    "surname_rank",
]
