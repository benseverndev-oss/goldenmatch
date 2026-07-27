"""Shared deterministic entity-level split helper for the multi-source ER data
pipeline (Task 2). CPU/box-safe: stdlib only, never imports torch/transformers.

`gen_pairs.py` delegates its local `_split_of` to this module so the split
logic has one source of truth across the synthetic generator and any
benchmark/real-dataset `PairSource` that needs entity-level train/val/test
partitioning."""
from __future__ import annotations

import hashlib


def split_of(eid: int | str, *, seed: int, val_frac: float, test_frac: float,
             holdout_domain: str | None = None, domain: str | None = None) -> str:
    """Deterministic entity-level split. Holdout domain -> 'test'; else hash
    (seed, str(eid)) into train/val/test. str(eid) so benchmark string IDs work."""
    if holdout_domain and domain == holdout_domain:
        return "test"
    h = hashlib.sha256(f"{seed}:{eid}".encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    if frac < test_frac:
        return "test"
    if frac < test_frac + val_frac:
        return "val"
    return "train"
