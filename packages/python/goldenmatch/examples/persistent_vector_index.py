#!/usr/bin/env python
"""Persistent vector index -- embed once, query across runs (#1088).

Build a semantic index over a column of records, persist it to disk, and reopen
it in a later run/process to query WITHOUT re-embedding. An incremental ``add``
extends the index; identical text is never re-embedded (an embedding cache).

Zero cloud: the in-house embedder + numpy ANN fallback need no network or torch.
"""
import tempfile
from pathlib import Path

import goldenmatch as gm
import polars as pl

kb = pl.DataFrame(
    {
        "name": ["Acme Corporation", "Globex Incorporated", "Initech Systems"],
        "industry": ["manufacturing", "logistics", "software"],
    }
)

index_dir = Path(tempfile.mkdtemp()) / "company_index"

# --- Run 1: build the index and persist it -------------------------------------
idx = gm.VectorIndex(index_dir, column="name").build(kb)
idx.save()
print(f"Run 1: built + saved {len(idx)} vectors (dim {idx.dim}) to {index_dir}\n")

# --- Run 2 (a fresh process would do exactly this): reopen, no re-embed --------
reopened = gm.VectorIndex.load(index_dir)
print(f"Run 2: reopened index with {len(reopened)} records (no re-embedding)")
for hit in reopened.query("acme", k=2):
    print(f"    {hit.score:.3f}  {hit.record['name']}  ({hit.record['industry']})")

# --- Incremental add: extend the index, then persist again ---------------------
reopened.add(pl.DataFrame({"name": ["Umbrella Corp"], "industry": ["biotech"]}))
reopened.save()
print(f"\nAfter add + save: {len(reopened)} records")
top = reopened.query("umbrella", k=1)[0]
print(f"    new record retrievable: {top.record['name']} ({top.score:.3f})")

# --- Metadata pre-filter -------------------------------------------------------
software = reopened.query("systems", k=5, filters={"industry": "software"})
print(f"\nFiltered to industry=software: {[h.record['name'] for h in software]}")
