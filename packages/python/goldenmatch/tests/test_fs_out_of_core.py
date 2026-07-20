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


def _partitions(result):
    return sorted(
        tuple(sorted(c["members"]))
        for c in result.clusters.values()
        if len(c.get("members", [])) > 1
    )


def test_pipeline_opt_in_parity(monkeypatch):
    """dedupe_df with GOLDENMATCH_FS_OUT_OF_CORE=1 must yield the same clusters
    as the default in-memory FS route."""
    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import GoldenMatchConfig

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    df = _make_dedupe_df().drop("__row_id__")
    cfg = GoldenMatchConfig(
        matchkeys=[_make_probabilistic_mk()],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend="bucket",
    )

    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "0")
    default = dedupe_df(df, config=cfg)
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "1")
    ooc = dedupe_df(df, config=cfg)

    assert _partitions(ooc) == _partitions(default)


def test_non_field_strategy_raises():
    df = _make_dedupe_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(strategy="ann", keys=[BlockingKeyConfig(fields=["zip"])])
    em = train_em(df, mk, blocks=[], blocking_fields=[])
    with pytest.raises(NotImplementedError):
        score_fs_out_of_core(df, blocking, mk, set(), em)


def test_streaming_output_routes_and_excludes_xform(tmp_path):
    """stream_fs_dedupe_output: unique=singletons, dupes=multi-member, golden=one
    per non-oversized multi cluster; __xform_* excluded; written via COPY."""
    import types

    import duckdb
    import pyarrow.parquet as pq
    from goldenmatch.backends.fs_out_of_core import stream_fs_dedupe_output

    con = duckdb.connect(":memory:")
    prep = pl.DataFrame({
        "__row_id__": [1, 2, 3, 4, 5, 6],
        "name": ["a", "a", "b", "c", "c", "c"],
        "__xform_name_x__": ["a", "a", "b", "c", "c", "c"],
    })
    con.register("p", prep.to_arrow())
    con.execute("CREATE TABLE prep AS SELECT * FROM p")
    con.unregister("p")
    # clusters: {1,2} multi, {3} singleton, {4,5,6} multi
    assignments = [(1, 10), (2, 10), (3, 20), (4, 30), (5, 30), (6, 30)]
    cfg = types.SimpleNamespace(golden_rules=None)

    res = stream_fs_dedupe_output(con, "prep", assignments, cfg, str(tmp_path))

    assert res["unique_count"] == 1   # row 3
    assert res["dupes_count"] == 5    # rows 1,2,4,5,6
    assert res["golden_count"] == 2   # clusters 10 and 30
    u = pq.read_table(res["unique_path"])
    assert "__xform_name_x__" not in u.column_names        # helper excluded
    assert u.column("__row_id__").to_pylist() == [3]
    d = pq.read_table(res["dupes_path"])
    assert sorted(d.column("__row_id__").to_pylist()) == [1, 2, 4, 5, 6]
    con.close()


def test_end_to_end_streaming_dedupe(tmp_path):
    """run_fs_dedupe_streaming: prep -> store -> score -> cluster -> streamed
    parquet output. Rows preserved (unique + dupes == N), planted dups found,
    a golden per multi-member cluster."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    res = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path))

    assert res["unique_count"] + res["dupes_count"] == df.height  # rows preserved
    assert res["dupes_count"] >= 2   # the planted same-zip dup pairs
    assert res["golden_count"] >= 1  # >=1 multi-member cluster -> a golden record


def test_streaming_link_threshold_filters_clustering():
    """run_fs_dedupe_streaming(link_threshold=...) clusters only pairs scoring
    >= the cut; an impossibly high cut leaves every record a singleton."""
    import tempfile
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    with tempfile.TemporaryDirectory() as d:
        res = run_fs_dedupe_streaming(
            df, blocking, mk, em, cfg, d, link_threshold=1e9
        )
    assert res["dupes_count"] == 0            # nothing clears the cut -> no dupes
    assert res["unique_count"] == df.height   # every record a singleton
    assert res["golden_count"] == 0


def _make_person_csv(path) -> int:
    """Write a person CSV with planted same-zip duplicates; return row count. No
    __row_id__ column (ingest assigns one)."""
    import csv as _csv

    rows = [
        ("John", "Smith", "90210"), ("Jon", "Smith", "90210"),
        ("Jane", "Doe", "10001"), ("Janet", "Doe", "10001"),
        ("Bob", "Jones", "60601"), ("Robert", "Jones", "60601"),
        ("Alice", "Brown", "30301"), ("Alicia", "Brown", "30301"),
        ("Tom", "Wilson", "20001"), ("Thomas", "Wilson", "20002"),
        ("Amy", "Clark", "90210"), ("Amie", "Clark", "90210"),
    ]
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["first_name", "last_name", "zip"])
        w.writerows(rows)
    return len(rows)


def _fs_person_config():
    from goldenmatch.config.schemas import GoldenMatchConfig

    return GoldenMatchConfig(
        matchkeys=[_make_probabilistic_mk()],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        backend="bucket",
    )


def test_dedupe_to_parquet_streaming(tmp_path, monkeypatch):
    """dedupe_to_parquet on a file with GOLDENMATCH_FS_OUT_OF_CORE=1 + an FS config
    routes through the streaming short-circuit: writes unique/dupes/golden parquet,
    rows preserved, streaming=True."""
    import pyarrow.parquet as pq
    from goldenmatch import dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "1")

    csv_path = tmp_path / "people.csv"
    n = _make_person_csv(csv_path)
    out_dir = tmp_path / "out"

    res = dedupe_to_parquet(
        str(csv_path), out_dir=str(out_dir), config=_fs_person_config()
    )

    assert res["streaming"] is True
    assert res["output_dir"] == str(out_dir)
    assert res["unique_count"] + res["dupes_count"] == n
    assert res["dupes_count"] >= 2
    # Files exist on disk with the reported row counts.
    assert pq.read_metadata(res["unique_path"]).num_rows == res["unique_count"]
    assert pq.read_metadata(res["dupes_path"]).num_rows == res["dupes_count"]
    if res["golden_count"]:
        assert pq.read_metadata(res["golden_path"]).num_rows == res["golden_count"]


def test_dedupe_to_parquet_fallback_when_flag_off(tmp_path, monkeypatch):
    """Flag OFF -> the in-memory pipeline runs and dedupe_to_parquet still writes
    the same parquet layout (streaming=False), with matching outputs."""
    import pyarrow.parquet as pq
    from goldenmatch import dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "0")

    csv_path = tmp_path / "people.csv"
    n = _make_person_csv(csv_path)
    out_dir = tmp_path / "out"

    res = dedupe_to_parquet(
        str(csv_path), out_dir=str(out_dir), config=_fs_person_config()
    )

    assert res["streaming"] is False
    assert res["unique_count"] + res["dupes_count"] == n
    assert pq.read_metadata(res["unique_path"]).num_rows == res["unique_count"]
    assert pq.read_metadata(res["dupes_path"]).num_rows == res["dupes_count"]


def _partition_set_from_parquet(dupes_path):
    """Rebuild the multi-member partition set from a streamed dupes.parquet
    (__cluster_id__ present) for parity comparison against in-memory clusters."""
    import polars as pl

    d = pl.read_parquet(dupes_path)
    parts = []
    for cid, sub in d.group_by("__cluster_id__"):
        parts.append(tuple(sorted(sub["__row_id__"].to_list())))
    return sorted(parts)


def test_dedupe_to_parquet_streaming_parity_with_in_memory(tmp_path, monkeypatch):
    """The streaming file output partitions the SAME records as the in-memory FS
    route (dedupe_df default) -- clusters match by (first_name,last_name,zip)."""
    import polars as pl
    from goldenmatch import dedupe_df, dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    csv_path = tmp_path / "people.csv"
    _make_person_csv(csv_path)
    df = pl.read_csv(csv_path)
    cfg = _fs_person_config()

    # In-memory reference: dedupe_df default path -> multi-member member-value sets.
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "0")
    mem = dedupe_df(df, config=cfg)
    mem_parts = _partitions(mem)  # tuples of member row_ids

    # Streaming file output.
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "1")
    out_dir = tmp_path / "out"
    res = dedupe_to_parquet(str(csv_path), out_dir=str(out_dir), config=cfg)
    assert res["streaming"] is True
    stream_parts = _partition_set_from_parquet(res["dupes_path"])

    # Both index rows 1..N in ingest order, so row_id partitions are comparable.
    assert stream_parts == sorted(mem_parts)
