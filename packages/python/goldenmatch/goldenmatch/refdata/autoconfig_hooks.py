"""Auto-config integration for the refdata packs.

Lets ``goldenmatch.core.autoconfig.build_matchkeys`` swap in refdata-aware
scorers / prepend refdata transforms when the column name signals a
specific person- or business-name shape.

Public entry point:

- ``refine_matchkey_field(column_name, scorer, transforms)`` -> ``(scorer, transforms)``.
  Called once per MatchkeyField being built. Returns either the input
  unchanged (no applicable refdata pack, or the column doesn't match a
  refdata-handled pattern) or a refined ``(scorer, transforms)`` tuple.

Refinement rules (all gated on the relevant pack's ``is_available()``):

1. Column matches ``last.?name|surname|lname|family.?name`` AND surname
   data is available → scorer becomes ``name_freq_weighted_jw``.
2. Column matches ``first.?name|given.?name|fname|forename`` AND
   given-name alias data is available → scorer becomes
   ``given_name_aliased_jw``.
3. Column matches ``company|business|org|firm|employer`` AND business
   data is available → ``legal_form_strip`` is prepended to the
   transform list (before any existing transforms like lowercase/strip).
4. Column matches ``address|street|addr|line.?1`` AND address data is
   available → ``address_normalize`` is prepended to the transform list.

A column that matches *both* (1) and (3) (e.g. "company_last_name", odd
but possible) takes the scorer swap from (1) and the transform from (3).
Refinements 3 and 4 prepend rather than replace, so the original
``lowercase``/``strip`` chain still runs after the refdata transform
collapses the trailing tokens — preserves backwards compat for
downstream blocking-key derivation.
"""
from __future__ import annotations

import re

# Patterns are tighter than ``autoconfig._NAME_PATTERNS`` on purpose:
# autoconfig's pattern only distinguishes "name-shaped" from anything else
# and lumps first/last/full together. These split the family vs. given
# distinction so we can pick the right refdata scorer.
_LAST_NAME_RE = re.compile(
    r"(^last.?name|^l.?name|^lname|surname|family.?name|"
    r"^last$|^surname$|^family$)",
    re.IGNORECASE,
)
_FIRST_NAME_RE = re.compile(
    r"(^first.?name|^f.?name|^fname|given.?name|forename|"
    r"^first$|^given$)",
    re.IGNORECASE,
)
_COMPANY_NAME_RE = re.compile(
    r"(company|business|org\b|organization|firm\b|employer|"
    r"corp.?name|company.?name|legal.?name|entity.?name)",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(
    r"(address|street|^addr$|addr.?line|line.?1|line.?2|"
    r"street.?address|mailing.?addr)",
    re.IGNORECASE,
)


def _surnames_available() -> bool:
    try:
        from goldenmatch.refdata.surnames import is_available
        return is_available()
    except Exception:
        return False


def _given_names_available() -> bool:
    try:
        from goldenmatch.refdata.given_names import is_available
        return is_available()
    except Exception:
        return False


def _business_available() -> bool:
    try:
        from goldenmatch.refdata.business import is_available
        return is_available()
    except Exception:
        return False


def _addresses_available() -> bool:
    try:
        from goldenmatch.refdata.addresses import is_available
        return is_available()
    except Exception:
        return False


def refine_matchkey_field(
    column_name: str,
    scorer: str,
    transforms: list[str],
) -> tuple[str, list[str]]:
    """Return a refdata-aware ``(scorer, transforms)`` tuple.

    Falls back to the input on any of: refdata not imported, the
    relevant pack's data file missing, no pattern match. Safe to call
    on every column unconditionally.
    """
    refined_scorer = scorer
    refined_transforms = list(transforms)  # copy — don't mutate caller's list

    # Scorer swaps. First match wins (last_name and first_name are
    # mutually exclusive in practice). Only applies when the input
    # scorer is a string-similarity scorer the refdata variant
    # supersedes; we leave 'exact' and 'embedding' alone.
    string_sim_scorers = {
        "jaro_winkler", "levenshtein", "token_sort", "ensemble", "dice", "jaccard",
    }
    if scorer in string_sim_scorers:
        if _LAST_NAME_RE.search(column_name) and _surnames_available():
            refined_scorer = "name_freq_weighted_jw"
        elif _FIRST_NAME_RE.search(column_name) and _given_names_available():
            refined_scorer = "given_name_aliased_jw"

    # Transform prepends. These compose with the scorer swap above —
    # e.g. a "company_last_name" column gets both. Prepend rather than
    # append so the existing lowercase/strip chain runs after our
    # canonicalization.
    if _COMPANY_NAME_RE.search(column_name) and _business_available():
        if "legal_form_strip" not in refined_transforms:
            refined_transforms.insert(0, "legal_form_strip")

    if _ADDRESS_RE.search(column_name) and _addresses_available():
        if "address_normalize" not in refined_transforms:
            refined_transforms.insert(0, "address_normalize")

    return refined_scorer, refined_transforms
