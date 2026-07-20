"""Out-of-core FS block scoring parity (`backends.fs_out_of_core`).

The streaming DuckDB-sourced scorer must emit the SAME pair set as the per-block
reference (build_blocks + the same FS kernel) that `score_buckets` is itself
parity-defined against — so out-of-core == score_buckets, transitively. Locks:
  1. static single-key parity.
  2. multi_pass parity (with cross-pass canonical dedup).
  3. non-field strategies raise NotImplementedError (caller falls back).
"""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.backends.fs_out_of_core import score_fs_out_of_core
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import build_blocks, collect_blocking_fields
from goldenmatch.core.probabilistic import (
    _fs_native_eligible,
    probabilistic_block_scorer,
    score_probabilistic_bucket_native,
    train_em,
)

from tests.test_probabilistic import _make_dedupe_df, _make_probabilistic_mk


def _bigger_df() -> pl.DataFrame:
    # A few real duplicate pairs across several zip blocks + a name pass, so
    # both passes carry pairs and cross-pass dedup is exercised.
    rows = []
    rid = 1
    fam = [
        ("John", "Smith", "90210"), ("Jon", "Smith", "90210"),
        ("Jane", "Doe", "10001"), ("Janet", "Doe", "10001"),
        ("Bob", "Jones", "60601"), ("Robert", "Jones", "60601"),
        ("Alice", "Brown", "30301"), ("Alicia", "Brown", "30301"),
        ("Tom", "Wilson", "20001"), ("Thomas", "Wilson", "20002"),
        ("Amy", "Clark", "90210"), ("Amie", "Clark", "90210"),
    ]
    for fn, ln, zp in fam:
        rows.append({"__row_id__": rid, "first_name": fn, "last_name": ln, "zip": zp})
        rid += 1
    return pl.DataFrame(rows)


def _reference_pairs(df, blocking, mk, em) -> set:
    """build_blocks + the same FS scorer score_buckets uses, deduped canonically
    in block order (matching score_fs_out_of_core's semantics)."""
    use_native = _fs_native_eligible(mk)
    prob = None if use_native else probabilistic_block_scorer(mk, em)
    seen: set = set()
    out: set = set()
    for b in build_blocks(df, blocking):
        bdf = b.materialize().native
        bpl = bdf if isinstance(bdf, pl.DataFrame) else pl.from_arrow(bdf)
        if bpl.height < 2:
            continue
        pairs = (
            score_probabilistic_bucket_native(bpl, [bpl.height], mk, em, frozenset())
            if use_native
            else prob(bpl, frozenset())
        )
        for a, c, s in pairs:
            key = (a, c) if a < c else (c, a)
            if key in seen:
                continue
            seen.add(key)
            out.add((key[0], key[1], round(float(s), 4)))
    return out


def _got_pairs(df, blocking, mk, em) -> set:
    return {
        (min(a, b), max(a, b), round(float(s), 4))
        for a, b, s in score_fs_out_of_core(df, blocking, mk, set(), em)
    }


def _train(df, blocking, mk):
    fields = collect_blocking_fields(blocking)
    return train_em(df, mk, blocks=build_blocks(df, blocking), blocking_fields=fields)


def test_static_parity():
    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    assert _got_pairs(df, blocking, mk, em) == _reference_pairs(df, blocking, mk, em)


def test_multipass_parity():
    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["zip"]),
            BlockingKeyConfig(fields=["last_name"]),
        ],
    )
    em = _train(df, blocking, mk)
    assert _got_pairs(df, blocking, mk, em) == _reference_pairs(df, blocking, mk, em)


def test_disk_spill_parity():
    """db_path='auto' spills the prepared table to a tempfile on disk; output
    must match the in-memory path."""
    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    mem = {
        (min(a, b), max(a, b), round(float(s), 4))
        for a, b, s in score_fs_out_of_core(df, blocking, mk, set(), em)
    }
    disk = {
        (min(a, b), max(a, b), round(float(s), 4))
        for a, b, s in score_fs_out_of_core(df, blocking, mk, set(), em, db_path="auto")
    }
    assert disk == mem == _reference_pairs(df, blocking, mk, em)


def test_non_field_strategy_raises():
    df = _make_dedupe_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(strategy="ann", keys=[BlockingKeyConfig(fields=["zip"])])
    em = train_em(df, mk, blocks=[], blocking_fields=[])
    with pytest.raises(NotImplementedError):
        score_fs_out_of_core(df, blocking, mk, set(), em)
