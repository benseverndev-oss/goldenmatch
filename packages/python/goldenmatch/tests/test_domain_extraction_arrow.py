"""Regression: domain extraction with LLM validation must not crash on the
arrow-native lane.

`_apply_domain_extraction` passes the pipeline frame to `llm_extract_features`
/ `apply_llm_extractions`, which are polars-native (`pl.col(...)` filter +
`with_columns`). On the arrow lane the frame is a `pa.Table`, which raised
`TypeError: unexpected argument type Expr` -- crashing domain extraction on
product/bibliographic data whenever an LLM key was present. The fix coerces to
polars for that LLM-gated block; this test locks it.
"""
from __future__ import annotations

import pyarrow as pa
from goldenmatch.config.schemas import DomainConfig, GoldenMatchConfig
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
