"""Regression: domain extraction with LLM validation must not crash on the
arrow-native lane.

`_apply_domain_extraction` passes the pipeline frame to `llm_extract_features`
/ `apply_llm_extractions`, which are polars-native (`pl.col(...)` filter +
`with_columns`). On the arrow lane the frame is a `pa.Table`, which raised
`TypeError: unexpected argument type Expr` -- crashing domain extraction on
product/bibliographic data whenever an LLM key was present. The fix coerces to
polars for that LLM-gated block; this test locks it.

The rest of the file locks the SECOND round of the same class, found by
bisecting the `benchmarks` Abt-Buy failure to #2250 (the polars->arrow
`dedupe_df` eviction). Two distinct defects, both only reachable once a polars
input started taking the arrow lane:

1. Stage ORDER -- the dedupe pipeline's arrow lane derived matchkeys BEFORE
   running domain extraction, so a matchkey over `__model__` hit
   `KeyError: 'Field "__model__" does not exist in schema'`.
2. Three un-ported `df.columns` reads in `core/domain.py`. `pa.Table.columns`
   is a list of ChunkedArrays, not names, so one raised
   (`'ChunkedArray' object has no attribute 'startswith'`) and two SILENTLY
   answered False for every membership test -- disabling product-subdomain
   detection and bibliographic extraction on the arrow lane with no error.
"""
from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.config.schemas import (
    DomainConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import llm_extract as LE
from goldenmatch.core.pipeline import _apply_domain_extraction


def _product_table():
    return pa.table({
        "__row_id__": list(range(6)),
        "title": ["Apple iPhone 12 64GB", "iPhone 12 Apple 64GB", "Sony WH-1000XM4",
                  "Bose QC45", "Dell XPS 13", "Lenovo X1"],
        "description": ["phone"] * 6,
        "manufacturer": ["apple", "apple", "sony", "bose", "dell", "lenovo"],
    })


def test_domain_extraction_llm_validation_on_arrow_table(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key")
    # Mock the network call so the LLM path runs offline; a non-empty extraction
    # also exercises apply_llm_extractions' polars write-back after the coerce.
    monkeypatch.setattr(
        LE, "_call_openai",
        lambda prompt, api_key, model: ('[{"brand":"apple","model":"iphone 12","color":null,"specs":null}]', 10, 5),
    )
    cfg = GoldenMatchConfig(domain=DomainConfig(
        enabled=True, mode="auto", llm_validation=True, confidence_threshold=0.99,
    ))
    # Must NOT raise TypeError on the pa.Table; returns a materialized frame.
    out = _apply_domain_extraction(_product_table(), cfg)
    assert out is not None
    # frame-shaped result (polars post-coerce; the caller re-normalizes via _tf_lane)
    assert hasattr(out, "columns")


def test_domain_extraction_arrow_no_llm_key_is_safe(monkeypatch):
    # Without a key, llm_extract abstains -> still must not crash on the arrow frame.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = GoldenMatchConfig(domain=DomainConfig(
        enabled=True, mode="auto", llm_validation=True, confidence_threshold=0.99,
    ))
    out = _apply_domain_extraction(_product_table(), cfg)
    assert out is not None


def _software_titles() -> list[str]:
    """Software-shaped text: software words, no electronics model numbers.

    `_detect_product_subdomain` scores `software` on these; the electronics
    signals (a `LETTERS+DIGITS` model number, or spec words) are deliberately
    absent so the two lanes have an unambiguous right answer to agree on.
    """
    return [
        "Microsoft Office Home and Student License Download",
        "Adobe Acrobat Standard Edition Upgrade",
        "Norton Antivirus Subscription Software",
    ]


def _biblio_profile():
    from goldenmatch.core.domain import DomainProfile as InternalProfile

    return InternalProfile(name="bibliographic", confidence=0.9, text_columns=["title"])


def test_domain_extraction_emits_profile_on_arrow_table():
    """`_emit_domain_profile` reads column NAMES; on a pa.Table they are arrays.

    The emitter is a no-op unless a capture is active, which is why the two
    tests above never reached this line -- the auto-config controller runs the
    pipeline under `profile_capture`, so in production it always did.
    Pre-fix: `AttributeError: 'pyarrow.lib.ChunkedArray' object has no
    attribute 'startswith'`.
    """
    from goldenmatch.core.profile_emitter import profile_capture

    cfg = GoldenMatchConfig(domain=DomainConfig(
        enabled=True, mode="auto", llm_validation=False,
    ))
    with profile_capture() as e:
        out = _apply_domain_extraction(_product_table(), cfg)

    assert out is not None
    assert e.domain is not None
    # The derived-column list is the payload that needed real column names.
    assert "__model__" in e.domain.derived_columns
    assert "__brand__" in e.domain.derived_columns


def test_product_subdomain_detection_agrees_across_lanes():
    """`text_col not in df.columns` is False for EVERY column on a pa.Table.

    Pre-fix that short-circuited `_detect_product_subdomain` to its
    "electronics" fallback on the arrow lane -- a silent wrong answer, not a
    crash, so software data was extracted with the electronics extractor.
    """
    from goldenmatch.core.domain import DomainProfile as InternalProfile
    from goldenmatch.core.domain import _detect_product_subdomain

    titles = _software_titles()
    polars_answer = _detect_product_subdomain(
        pl.DataFrame({"title": titles}),
        InternalProfile(name="product", confidence=0.9, text_columns=["title"]),
    )
    arrow_answer = _detect_product_subdomain(
        pa.table({"title": titles}),
        InternalProfile(name="product", confidence=0.9, text_columns=["title"]),
    )
    assert polars_answer == "software"
    assert arrow_answer == polars_answer


def test_biblio_extraction_adds_title_key_on_arrow_table():
    """Same membership bug at the biblio extractor's guard.

    Pre-fix `_extract_biblio_features_df` returned the frame UNCHANGED on the
    arrow lane -- no `__title_key__`, no error, so a matchkey over it would
    then fail far away from the cause.
    """
    from goldenmatch.core.domain import extract_features
    from goldenmatch.core.frame import to_frame

    titles = ["The Anatomy of a Large-Scale Hypertextual Web Search Engine",
              "Authoritative Sources in a Hyperlinked Environment"]
    out, _low_conf = extract_features(pa.table({"title": titles}), _biblio_profile())

    assert "__title_key__" in to_frame(out).columns
    assert to_frame(out).height == 2


# ── Stage order: domain extraction must precede matchkey derivation ──────────

def _domain_matchkey_config() -> GoldenMatchConfig:
    """Explicit config whose only matchkey is over a DERIVED domain column.

    This is the shape auto-config produces on product data, reduced to the one
    thing that matters: the matchkey cannot be derived until domain extraction
    has materialized `__model__`.
    """
    return GoldenMatchConfig(
        domain=DomainConfig(enabled=True, mode="auto", llm_validation=False),
        matchkeys=[MatchkeyConfig(
            name="model_exact", type="exact",
            fields=[MatchkeyField(field="__model__")],
        )],
    )


def _product_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "title": [
            "Sony STR-DA3600ES receiver", "Sony STR-DA3600ES 7.2 receiver",
            "Canon EOS 60D camera", "Canon EOS 60D DSLR",
            "Bose QC15 headphones", "Bose QC15 noise cancelling",
        ],
        "description": ["av receiver", "av receiver", "camera", "camera",
                        "audio", "audio"],
    })


def test_arrow_lane_matchkey_over_derived_domain_column():
    """The benchmarks Abt-Buy failure, reduced.

    A polars input evicts to the arrow lane (#2250). Pre-fix that lane derived
    matchkeys BEFORE domain extraction, so this raised
    `KeyError: 'Field "__model__" does not exist in schema'`.
    """
    import goldenmatch as gm

    res = gm.dedupe_df(_product_frame(), config=_domain_matchkey_config())
    assert res is not None
    assert len(getattr(res, "clusters", None) or {}) > 0


def test_arrow_lane_runs_domain_extraction_before_matchkeys():
    """Pin the ORDER itself, not just the absence of the crash.

    The classic lane and the match pipeline's arrow lane both run
    domain_extraction -> memory_pre_overlay -> compute_matchkeys; the dedupe
    arrow lane must too, or the next derived-column matchkey re-breaks it.
    """
    import goldenmatch as gm
    from goldenmatch.core.bench import bench_capture

    with bench_capture() as rec:
        gm.dedupe_df(_product_frame(), config=_domain_matchkey_config())

    stages = list(rec.timings)
    assert "domain_extraction" in stages
    assert "compute_matchkeys" in stages
    assert stages.index("domain_extraction") < stages.index("compute_matchkeys")


@pytest.mark.parametrize("lane", ["arrow", "classic"])
def test_derived_column_matchkey_parity_across_lanes(monkeypatch, lane):
    """Both lanes produce the same clusters for a derived-column matchkey.

    `GOLDENMATCH_FRAME_LANE=0` is the documented revert switch to the classic
    shim; it was the only working path for this config before the fix.
    """
    import goldenmatch as gm

    if lane == "classic":
        monkeypatch.setenv("GOLDENMATCH_FRAME_LANE", "0")
    else:
        monkeypatch.delenv("GOLDENMATCH_FRAME_LANE", raising=False)

    res = gm.dedupe_df(_product_frame(), config=_domain_matchkey_config())
    sizes = sorted(
        len(c["members"] if isinstance(c, dict) else c.members)
        for c in (getattr(res, "clusters", None) or {}).values()
    )
    assert sizes == [1, 1, 2, 2]
