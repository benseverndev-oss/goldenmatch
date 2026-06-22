"""Embedding ops -- drift, per-field models, canonicalization eval (#1093).

Fully deterministic + offline: numpy arrays for precise drift control, the
zero-config in-house embedder for an integration check, and ``canonicalize_cluster``
(deterministic / stubbed LLM) for the canonicalization eval. No network, no torch.
"""
from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest
from goldenmatch.core.embedding_ops import (
    CanonicalizationEval,
    EmbeddingDriftReport,
    embedding_drift,
    evaluate_canonicalization,
    select_field_model,
    select_field_models,
)
from goldenmatch.core.llm_canonicalize import canonicalize_cluster


def _normalize(a: np.ndarray) -> np.ndarray:
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def _cone(direction, n, *, noise=0.1, seed=0):
    """A tight cluster of unit vectors around ``direction`` (a directional centroid)."""
    rng = np.random.default_rng(seed)
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    pts = d + noise * rng.standard_normal((n, len(d)))
    return _normalize(pts)


# ── drift detection ──────────────────────────────────────────────────────────


def test_identical_sets_no_drift():
    rng = np.random.default_rng(1)
    a = _normalize(rng.standard_normal((100, 16)))
    rep = embedding_drift(a, a.copy())
    assert isinstance(rep, EmbeddingDriftReport)
    assert rep.psi == pytest.approx(0.0, abs=1e-9)
    assert rep.centroid_cosine_drift == pytest.approx(0.0, abs=1e-9)
    assert rep.drifted is False


def test_shifted_distribution_alarms():
    rng = np.random.default_rng(2)
    ref = _normalize(rng.standard_normal((200, 16)))
    cur = _normalize(ref + 1.5)  # push the whole space along (1,1,...)
    rep = embedding_drift(ref, cur)
    assert rep.psi > 0.25
    assert rep.drifted is True


def test_diffuse_independent_sets_no_false_alarm():
    # Two independent mean-zero sets: the centroid direction is noise -- the cone
    # guard must keep centroid-cosine OUT of the alarm so this does NOT trip.
    rng = np.random.default_rng(3)
    ref = _normalize(rng.standard_normal((300, 16)))
    cur = _normalize(rng.standard_normal((300, 16)))
    rep = embedding_drift(ref, cur)
    assert rep.psi < 0.25
    assert rep.drifted is False


def test_directional_centroid_cosine_path():
    # A real (directional) centroid: a rotation alarms via centroid cosine even
    # when PSI is forced not to alarm; the diffuse case stays quiet under the
    # same forced-PSI setting (proving the cone guard).
    ref = _cone([1, 0, 0, 0], 100, seed=10)
    rotated = _cone([0, 1, 0, 0], 100, seed=11)
    rep = embedding_drift(ref, rotated, alarm_psi=1e9)
    assert rep.centroid_cosine_drift > 0.1
    assert rep.drifted is True  # centroid path fires (directional centroid)

    rng = np.random.default_rng(12)
    diffuse_ref = _normalize(rng.standard_normal((300, 16)))
    diffuse_cur = _normalize(rng.standard_normal((300, 16)))
    quiet = embedding_drift(diffuse_ref, diffuse_cur, alarm_psi=1e9)
    assert quiet.drifted is False  # guard suppresses the noisy-centroid alarm


def test_identical_inhouse_embeddings_no_drift():
    from goldenmatch.core.embedder import get_embedder

    emb = get_embedder("inhouse")
    vals = ["Acme Corp", "Globex Inc", "Initech LLC", "Stark Industries"] * 6
    ref = emb.embed_column(vals, cache_key="ops_ref")
    cur = emb.embed_column(list(vals), cache_key="ops_cur")  # same strings
    rep = embedding_drift(ref, cur)
    assert rep.drifted is False


def test_dim_mismatch_raises():
    with pytest.raises(ValueError, match="dim mismatch"):
        embedding_drift(np.zeros((4, 8)), np.zeros((4, 16)))


def test_empty_inputs_no_drift_no_raise():
    rep = embedding_drift(np.zeros((0, 16)), np.zeros((0, 16)))
    assert rep.drifted is False
    assert rep.psi == 0.0


def test_alarm_thresholds_respected():
    rng = np.random.default_rng(4)
    ref = _normalize(rng.standard_normal((200, 16)))
    cur = _normalize(ref + 1.5)
    # An impossibly high PSI threshold + directional guard off -> never alarms.
    assert embedding_drift(ref, cur, alarm_psi=1e9).drifted is False


def test_drift_report_serializable():
    rng = np.random.default_rng(5)
    a = _normalize(rng.standard_normal((50, 16)))
    blob = json.dumps(embedding_drift(a, a.copy()).as_dict())
    assert "psi" in blob and "drifted" in blob


# ── per-field model selection ────────────────────────────────────────────────


def test_short_vs_long_text_routing():
    df = pl.DataFrame(
        {
            "name": ["Acme Corp", "Globex Inc", "Initech LLC"],
            "description": [
                "A lengthy paragraph describing the firm operations across many markets",
                "Another long description of products, history, and global footprint here",
                "Detailed multi-sentence overview of the organization and its many ventures",
            ],
        }
    )
    choices = select_field_models(df)
    assert choices["name"].model == "inhouse"
    assert choices["name"].reason == "short-text"
    assert choices["description"].model == "all-MiniLM-L6-v2"
    assert choices["description"].reason == "long-text"


def test_override_always_wins():
    df = pl.DataFrame({"name": ["Acme Corp"], "description": ["x" * 100]})
    choices = select_field_models(df, overrides={"description": "text-embedding-3-large"})
    assert choices["description"].model == "text-embedding-3-large"
    assert choices["description"].reason == "override"


def test_non_text_columns_skipped():
    df = pl.DataFrame({"name": ["Acme"], "zip": [90210], "count": [3]})
    choices = select_field_models(df)
    assert "name" in choices
    assert "zip" not in choices and "count" not in choices


def test_select_field_model_single_and_defaults():
    assert select_field_model("name", None).model == "inhouse"  # no values -> short
    assert select_field_model("x", ["short"], override="custom").model == "custom"
    long_choice = select_field_model("desc", ["q" * 80, "w" * 90])
    assert long_choice.model == "all-MiniLM-L6-v2"
    assert isinstance(long_choice.as_dict(), dict)


# ── canonicalization quality eval ────────────────────────────────────────────

RECS = [
    {"name": "Bob", "email": "bob@x.com", "phone": None},
    {"name": "Robert Smith", "email": "bob@x.com", "phone": "555-1234"},
]


def test_completeness_and_provenance_no_gold():
    cr = canonicalize_cluster(RECS)
    ev = evaluate_canonicalization([cr])
    assert isinstance(ev, CanonicalizationEval)
    assert ev.n_records == 1
    assert ev.field_completeness == 1.0  # every canonical cell filled
    assert ev.provenance_coverage == 1.0  # every value traced to a source record
    assert ev.synthesized_rate == 0.0
    assert ev.llm_rate == 0.0
    assert "field_accuracy" not in ev.summary()  # omitted without a gold


def test_field_accuracy_against_gold():
    cr = canonicalize_cluster(RECS)
    ev = evaluate_canonicalization(
        [cr], gold=[{"name": "Robert Smith", "phone": "555-1234"}]
    )
    assert ev.tp == 2 and ev.fp == 0 and ev.fn == 0
    assert ev.precision == 1.0 and ev.recall == 1.0 and ev.f1 == 1.0


def test_wrong_value_counts_fp_and_fn():
    cr = canonicalize_cluster(RECS)
    ev = evaluate_canonicalization([cr], gold=[{"name": "Someone Else"}])
    assert ev.fn == 1  # we didn't produce the expected name
    assert ev.fp == 1  # we produced a different non-null value
    assert ev.recall == 0.0


def test_synthesized_value_lowers_provenance():
    # The LLM invents a value present in no source record -> synthesized (no source).
    payload = {"fields": {"name": {"value": "Bobby Smith", "source": 9}}, "rationale": "r"}
    cr = canonicalize_cluster(
        RECS, fields=["name"], llm_call=lambda p: (json.dumps(payload), 1, 1)
    )
    ev = evaluate_canonicalization([cr])
    assert ev.synthesized_rate == 1.0
    assert ev.provenance_coverage == 0.0
    assert ev.llm_rate == 1.0


def test_llm_rate_mixed_batch():
    det = canonicalize_cluster(RECS)  # deterministic
    payload = {"fields": {"name": {"value": "Bob", "source": 0}}, "rationale": "r"}
    llm = canonicalize_cluster(RECS, llm_call=lambda p: (json.dumps(payload), 1, 1))
    ev = evaluate_canonicalization([det, llm])
    assert ev.n_records == 2
    assert ev.llm_rate == 0.5


def test_empty_records():
    ev = evaluate_canonicalization([])
    assert ev.n_records == 0
    assert ev.field_completeness == 0.0
    assert ev.summary()["n_records"] == 0
