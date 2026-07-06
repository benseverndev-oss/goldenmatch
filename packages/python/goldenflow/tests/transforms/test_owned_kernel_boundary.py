"""W6 owned-kernel boundary guard.

The owned-kernel program (goldenflow-core is the reference; Python/TS/wasm/
DuckDB/Postgres conform byte-for-byte) covers the transforms that are
single-value, deterministic, and byte-portable. This test is the "no silent
gap" enforcement: EVERY registered transform must be explicitly classified
into exactly one bucket, so a newly added transform can't slip in without an
honest decision about whether it is owned or a documented structural hole.

The boundary rationale lives in
``docs/design/2026-07-06-goldenflow-owned-kernel-boundary.md``. If you add a
transform and this test fails, put it in the right bucket below (and, if it is
owned, add it to the byte-parity corpus or a pinned-vector kernel test).
"""
from __future__ import annotations

import json
from pathlib import Path

import goldenflow  # noqa: F401 -- import-time transform registration

# Force-register the ON-DEMAND transform surface so this guard enumerates the
# FULL registry deterministically, independent of test ordering. The domain
# packs (goldenflow/domains/*, normally loaded via --domain) and the LLM
# corrector (normally imported via --llm) both self-register at MODULE import,
# but goldenflow/__init__ does NOT import them -- so without these imports,
# registry() would contain them only if some earlier test happened to load a
# domain/LLM path first (an order-dependent false pass/fail).
import goldenflow.domains.carceral  # noqa: E402,F401
import goldenflow.domains.ecommerce  # noqa: E402,F401
import goldenflow.domains.finance  # noqa: E402,F401
import goldenflow.domains.healthcare  # noqa: E402,F401
import goldenflow.domains.people_hr  # noqa: E402,F401
import goldenflow.domains.real_estate  # noqa: E402,F401
import goldenflow.llm.corrector  # noqa: E402,F401
from goldenflow.transforms import registry  # noqa: E402

_CORPUS_PATH = Path(__file__).parent.parent / "parity" / "identifiers_corpus.jsonl"


def _corpus_transforms() -> set[str]:
    names: set[str] = set()
    with _CORPUS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                names.add(json.loads(line)["transform"])
    return names


# --- Non-corpus buckets (each an explicit, documented structural decision) ---

# Owned kernels whose byte-parity is proven by PINNED-VECTOR kernel tests
# rather than the string-keyed corpus, because their shape doesn't fit a
# string->scalar row: multi-output splits, a multi-input merge, parameterized
# text ops, numeric-ARRAY ops (numeric input, not string), the NANP-gated phone
# encoders, the whole-column fuzzy autocorrect, and the has_initial-delegating
# flag wrapper. See tests/transforms/test_{name,address,text,numeric,
# autocorrect}_kernels.py + test_native_parity.py.
_OWNED_PINNED = frozenset(
    {
        "split_name",
        "split_name_reverse",
        "split_address",
        "merge_name",
        "truncate",
        "pad_left",
        "pad_right",
        "round",
        "clamp",
        "abs_value",
        "fill_zero",
        "phone_e164",
        "phone_national",
        "phone_country_code",
        "category_auto_correct",
        "initial_expand",  # value passthrough; flags via has_initial (corpus-owned)
    }
)

# Data-dependent: the transform applies a CALLER-SUPPLIED variant->canonical
# mapping (a function param or a CSV/YAML loaded at runtime). That mapping is
# runtime DATA, not logic, so goldenflow-core does NOT own a dict-lookup kernel
# for it. The shared key-derivation IS owned (category_normalize_key, in the
# corpus); only the lookup-with-fallback loop stays in the host.
_DATA_DEPENDENT = frozenset({"category_standardize", "category_from_file"})

# Structurally non-byte-portable: date parsing/formatting depends on
# dateutil's fuzzy parser + non-deterministic partial-date resolution, which
# MEASURABLY cannot be reproduced byte-for-byte in Rust/TS (see the memory
# reference_dates_chrono_dateutil_parity + the boundary design doc). Dates stay
# Python-reference; Polars already vectorizes them, so there is no perf gap.
_DEFERRED_DATES = frozenset(
    {
        "date_iso8601",
        "date_us",
        "date_eu",
        "date_parse",
        "date_shift",
        "date_validate",
        "datetime_iso8601",
        "age_from_dob",
        "extract_year",
        "extract_month",
        "extract_day",
        "extract_quarter",
        "extract_day_of_week",
    }
)

# Deliberately pure-Python reference (no byte-parity native), for two distinct
# reasons:
#   - phone_validate: its only native symbol (phone_valid_arrow) implements
#     `is_valid`, NOT the product-chosen `is_possible` spec, so it is
#     intentionally unwired (_FALLBACK_ONLY in _native_loader).
#   - category_llm_correct: it calls an LLM (goldenflow.llm.corrector) for
#     categorical correction, so its output is inherently non-deterministic and
#     can NEVER be reproduced byte-for-byte in an owned kernel. Host-only by
#     construction (activated by --llm / GOLDENFLOW_LLM).
_REFERENCE_ONLY = frozenset({"phone_validate", "category_llm_correct"})

# Domain-pack transforms: registered by the domain packs under
# ``goldenflow/domains/*`` (people_hr, healthcare, finance, ecommerce,
# real_estate, carceral). They are ``auto_apply=False`` (only run when a
# ``--domain <name>`` pack is loaded) and are Python-only reference transforms
# that have NOT been ported to goldenflow-core owned kernels -- so they are out
# of scope for the byte-parity corpus. This is a deliberate boundary, not a gap:
# the owned-kernel program covers the core transform surface first; a domain
# transform graduates into the corpus if/when it is promoted to an owned kernel.
# Add a newly registered ``domains/*`` transform here (until it's promoted).
_DOMAIN_PACK = frozenset(
    {
        "ssn_validate",  # people_hr
        "icd10_format",  # healthcare
        "account_mask",  # finance
        "cusip_format",  # finance
        "sku_normalize",  # ecommerce
        "mls_normalize",  # real_estate
        "carceral_abbreviate",  # carceral
        "carceral_name_normalize",  # carceral
        "carceral_org_strip",  # carceral
        "latlng_pack",  # carceral
    }
)

_NON_CORPUS_BUCKETS = (
    _OWNED_PINNED
    | _DATA_DEPENDENT
    | _DEFERRED_DATES
    | _REFERENCE_ONLY
    | _DOMAIN_PACK
)


def test_every_registered_transform_is_classified() -> None:
    """No silent gaps: every registered transform is either in the byte-parity
    corpus or in exactly one documented non-corpus bucket."""
    registered = set(registry())
    corpus = _corpus_transforms()
    classified = corpus | _NON_CORPUS_BUCKETS

    unclassified = registered - classified
    assert not unclassified, (
        "unclassified transform(s) -- add each to the byte-parity corpus (if it "
        "is an owned single-value kernel) or to a bucket in this test with a "
        f"documented rationale: {sorted(unclassified)}"
    )


def test_buckets_have_no_stale_entries() -> None:
    """Every bucketed name is actually registered (catches renamed/removed
    transforms left behind in a bucket)."""
    registered = set(registry())
    stale = _NON_CORPUS_BUCKETS - registered
    assert not stale, f"bucketed transform(s) no longer registered: {sorted(stale)}"


def test_buckets_are_disjoint_from_corpus() -> None:
    """The non-corpus buckets must not double-classify a corpus-owned
    transform (a transform is owned-via-corpus XOR bucketed)."""
    corpus = _corpus_transforms()
    overlap = corpus & _NON_CORPUS_BUCKETS
    assert not overlap, f"transform(s) both in corpus and a bucket: {sorted(overlap)}"


def test_buckets_are_mutually_disjoint() -> None:
    """Each non-corpus transform lives in exactly one bucket."""
    buckets = {
        "owned_pinned": _OWNED_PINNED,
        "data_dependent": _DATA_DEPENDENT,
        "deferred_dates": _DEFERRED_DATES,
        "reference_only": _REFERENCE_ONLY,
        "domain_pack": _DOMAIN_PACK,
    }
    seen: dict[str, str] = {}
    for label, names in buckets.items():
        for name in names:
            assert name not in seen, (
                f"{name} is in both {seen[name]} and {label} buckets"
            )
            seen[name] = label
