#!/usr/bin/env python
"""Embedding ops -- drift alarms, per-field models, canonicalization quality (#1093).

The operations layer for running embeddings + canonicalization in production:

1. Detect when the embedding model's output distribution has SHIFTED (drift alarm).
2. Pick the right embedder PER FIELD (names vs descriptions).
3. MEASURE canonicalization quality (completeness, provenance, accuracy).

Zero cloud: the in-house embedder + numpy stats need no network or torch.
"""
import goldenmatch as gm
import polars as pl
from goldenmatch.core.embedder import get_embedder

emb = get_embedder("inhouse")

# 1. ── Drift detection ────────────────────────────────────────────────────────
# Embed a reference batch, then compare a later batch against it.
reference = emb.embed_column(
    ["Acme Corporation", "Globex Inc", "Initech LLC", "Stark Industries"] * 8,
    cache_key="ref",
)
later_same = emb.embed_column(
    ["Acme Corporation", "Globex Inc", "Initech LLC", "Stark Industries"] * 8,
    cache_key="same",
)
later_shifted = emb.embed_column(
    ["lorem ipsum dolor", "the quick brown fox", "unrelated topic", "noise words"] * 8,
    cache_key="shift",
)

no_drift = gm.embedding_drift(reference, later_same)
drift = gm.embedding_drift(reference, later_shifted)
print("1. Drift detection")
print(f"   same inputs   -> drifted={no_drift.drifted}  psi={no_drift.psi:.3f}")
print(f"   shifted inputs-> drifted={drift.drifted}  psi={drift.psi:.3f}  "
      f"(ALARM)" if drift.drifted else "")

# 2. ── Per-field model selection ──────────────────────────────────────────────
kb = pl.DataFrame(
    {
        "name": ["Acme Corp", "Globex Inc", "Initech LLC"],
        "description": [
            "A multinational manufacturer of industrial widgets and precision tooling",
            "Global logistics and supply-chain operator serving enterprise clients",
            "Boutique software consultancy specialising in legacy system modernisation",
        ],
    }
)
print("\n2. Per-field model selection")
for col, choice in gm.select_field_models(kb).items():
    print(f"   {col:<12} -> {choice.model:<18} ({choice.reason}, "
          f"mean {choice.mean_chars:.0f} chars)")

# 3. ── Canonicalization quality eval ──────────────────────────────────────────
clusters = [
    [
        {"name": "Bob", "email": "bob@x.com", "phone": None},
        {"name": "Robert Smith", "email": "bob@x.com", "phone": "555-1234"},
    ],
    [
        {"name": "Jane Doe", "email": "jane@y.com", "phone": "555-9999"},
        {"name": "Jane A. Doe", "email": None, "phone": "555-9999"},
    ],
]
canon = [gm.canonicalize_cluster(c) for c in clusters]
gold = [{"name": "Robert Smith", "phone": "555-1234"}, {"name": "Jane A. Doe"}]
quality = gm.evaluate_canonicalization(canon, gold=gold)
print("\n3. Canonicalization quality")
for key, val in quality.summary().items():
    print(f"   {key}: {val}")
