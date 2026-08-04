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


def _arrow_pairs(df, blocking, mk, em) -> set:
    """The `emit='arrow'` PAIR_STREAM table, deduped canonically, as a pair set
    (scores dropped — the arrow stream keeps dup edges, deduped downstream)."""
    tbl = score_fs_out_of_core(df, blocking, mk, set(), em, emit="arrow")
    a = tbl.column("id_a").to_pylist()
    b = tbl.column("id_b").to_pylist()
    return {(min(x, y), max(x, y)) for x, y in zip(a, b)}


def test_emit_arrow_pair_set_matches_tuples():
    """`emit='arrow'` emits the SAME canonical pair set as `emit='tuples'` (the
    scored pairs, before/after dedup fold to the same edges — Union-Find safe)."""
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
    tuples_set = {(min(a, b), max(a, b)) for a, b, _ in
                  score_fs_out_of_core(df, blocking, mk, set(), em)}
    assert _arrow_pairs(df, blocking, mk, em) == tuples_set


def test_arrow_stream_and_python_cluster_paths_agree(tmp_path, monkeypatch):
    """The Arrow-native clustering path (default) and the `GOLDENMATCH_FS_OOC_ARROW_CLUSTER=0`
    Python Union-Find path partition the records IDENTICALLY."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_OOC_ARROW_CLUSTER", "1")
    res_arrow = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path / "a"))
    monkeypatch.setenv("GOLDENMATCH_FS_OOC_ARROW_CLUSTER", "0")
    res_py = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path / "p"))

    assert _partition_set_from_parquet(res_arrow["dupes_path"]) == \
        _partition_set_from_parquet(res_py["dupes_path"])
    assert res_arrow["unique_count"] == res_py["unique_count"]
    assert res_arrow["dupes_count"] == res_py["dupes_count"]


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


def test_streaming_output_removes_stale_golden_on_reuse(tmp_path):
    """No multi-member clusters this run -> golden_path None AND a golden.parquet
    left by a prior run into the same out_dir is removed (file set matches the
    returned paths)."""
    import types

    import duckdb
    from goldenmatch.backends.fs_out_of_core import stream_fs_dedupe_output

    # a golden.parquet from a "prior run" already sits in the out_dir
    stale = tmp_path / "golden.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(stale)
    assert stale.exists()

    con = duckdb.connect(":memory:")
    prep = pl.DataFrame({"__row_id__": [1, 2, 3], "name": ["a", "b", "c"]})
    con.register("p", prep.to_arrow())
    con.execute("CREATE TABLE prep AS SELECT * FROM p")
    con.unregister("p")
    assignments = [(1, 10), (2, 20), (3, 30)]  # all singletons -> no golden
    res = stream_fs_dedupe_output(
        con, "prep", assignments, types.SimpleNamespace(golden_rules=None), str(tmp_path)
    )
    con.close()

    assert res["golden_count"] == 0
    assert res["golden_path"] is None
    assert not stale.exists()  # stale prior-run golden removed


def test_prep_all_ids_range_when_contiguous_else_list():
    """_prep_all_ids returns a range (no 25-50M-elem list) when __row_id__ is
    contiguous, and falls back to an explicit list when there are gaps."""
    import duckdb
    from goldenmatch.backends.fs_out_of_core import _prep_all_ids

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE prep(__row_id__ BIGINT)")
    con.executemany("INSERT INTO prep VALUES (?)", [(i,) for i in range(6)])
    got = _prep_all_ids(con)
    assert isinstance(got, range) and list(got) == [0, 1, 2, 3, 4, 5]

    con.execute("DELETE FROM prep WHERE __row_id__ = 3")  # introduce a gap
    got = _prep_all_ids(con)
    assert isinstance(got, list) and sorted(got) == [0, 1, 2, 4, 5]

    con.execute("DELETE FROM prep")
    assert list(_prep_all_ids(con)) == []
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


# ── resolve_fs_block_source: the single knob unifying the two streaming lanes ──

def test_resolve_fs_block_source_default_is_eager(monkeypatch):
    from goldenmatch.backends.fs_out_of_core import (
        fs_out_of_core_enabled,
        resolve_fs_block_source,
    )

    monkeypatch.delenv("GOLDENMATCH_FS_BLOCK_SOURCE", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)
    assert resolve_fs_block_source() == "eager"
    assert fs_out_of_core_enabled() is False


@pytest.mark.parametrize(
    "block_source,expected",
    [("frame", "frame"), ("duckdb", "duckdb"), ("eager", "eager"),
     ("sequential", "sequential"), (" Sequential ", "sequential"),
     ("spill", "spill"), (" Spill ", "spill"),
     ("bucketed", "bucketed"), (" Bucketed ", "bucketed"),
     ("auto", "eager"), ("AUTO", "eager"), (" DuckDB ", "duckdb"),
     ("nonsense", "eager")],
)
def test_resolve_fs_block_source_reads_block_source_env(
    monkeypatch, block_source, expected
):
    from goldenmatch.backends.fs_out_of_core import resolve_fs_block_source

    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", block_source)
    assert resolve_fs_block_source() == expected


def test_resolve_fs_block_source_legacy_out_of_core_alias(monkeypatch):
    """GOLDENMATCH_FS_OUT_OF_CORE=1 is honored as a duckdb alias when
    GOLDENMATCH_FS_BLOCK_SOURCE is unset/auto/eager (back-compat)."""
    from goldenmatch.backends.fs_out_of_core import (
        fs_out_of_core_enabled,
        resolve_fs_block_source,
    )

    monkeypatch.delenv("GOLDENMATCH_FS_BLOCK_SOURCE", raising=False)
    monkeypatch.setenv("GOLDENMATCH_FS_OUT_OF_CORE", "1")
    assert resolve_fs_block_source() == "duckdb"
    assert fs_out_of_core_enabled() is True

    # An explicit recognized FS_BLOCK_SOURCE wins over the legacy flag.
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "frame")
    assert resolve_fs_block_source() == "frame"
    assert fs_out_of_core_enabled() is False


def test_block_source_frame_drives_bucket_streaming(monkeypatch):
    """score_buckets._fs_bounded_stream_enabled reads the SAME resolver, so
    FS_BLOCK_SOURCE=frame turns on in-RAM bounded bucket streaming."""
    from goldenmatch.backends.score_buckets import _fs_bounded_stream_enabled

    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "frame")
    assert _fs_bounded_stream_enabled() is True
    # duckdb / eager are NOT the in-bucket streaming lane.
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "duckdb")
    assert _fs_bounded_stream_enabled() is False
    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "auto")
    assert _fs_bounded_stream_enabled() is False


def test_end_to_end_sequential_dedupe(tmp_path):
    """run_fs_dedupe_sequential: in-RAM score -> Rust WCC -> streamed parquet.
    Rows preserved, planted dups found, a golden per multi-member cluster."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_sequential

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    res = run_fs_dedupe_sequential(df, blocking, mk, em, cfg, str(tmp_path))

    assert res["sequential"] is True
    assert res["unique_count"] + res["dupes_count"] == df.height
    assert res["dupes_count"] >= 2
    assert res["golden_count"] >= 1


def test_sequential_and_duckdb_orchestrators_agree(tmp_path):
    """run_fs_dedupe_sequential and run_fs_dedupe_streaming write the SAME
    unique/dupes/golden counts (same links -> same partition -> same output).

    ``pairs`` is NOT compared: the sequential path clusters through the fused FS
    Rust kernel, which returns cluster assignments directly and never surfaces an
    edge count (``pairs`` is ``None``), while the DuckDB path threads an edge
    stream. The output-row counts are the cross-orchestrator contract."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        run_fs_dedupe_sequential,
        run_fs_dedupe_streaming,
    )

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    seq = run_fs_dedupe_sequential(df, blocking, mk, em, cfg, str(tmp_path / "seq"))
    ooc = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path / "ooc"))
    for k in ("unique_count", "dupes_count", "golden_count"):
        assert seq[k] == ooc[k], k


def test_end_to_end_spill_dedupe(tmp_path):
    """run_fs_dedupe_spill: score -> per-pass edge shards on disk -> external WCC ->
    streamed parquet. DuckDB-free. Rows preserved, planted dups found, a golden per
    multi-member cluster."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_spill

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    res = run_fs_dedupe_spill(df, blocking, mk, em, cfg, str(tmp_path))

    assert res["spill"] is True
    assert res["streaming"] is True
    assert res["unique_count"] + res["dupes_count"] == df.height
    assert res["dupes_count"] >= 2
    assert res["golden_count"] >= 1


def test_spill_force_shard_runs_edge_path_same_partition(tmp_path, monkeypatch):
    """GOLDENMATCH_FS_SPILL_FORCE_SHARD=1 SKIPS the fused short-circuit so the
    actual edge-shard spill mechanism runs (res['fused'] is False, a real pair
    count), and its partition is IDENTICAL to the fused-first default (both
    cluster the same edge set)."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_spill

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    # Default: fused-first (this config is fused-covered).
    monkeypatch.delenv("GOLDENMATCH_FS_SPILL_FORCE_SHARD", raising=False)
    fused = run_fs_dedupe_spill(df, blocking, mk, em, cfg, str(tmp_path / "fused"))
    assert fused["fused"] is True  # fused short-circuit took it (no pair list)

    # Forced: skip fused, exercise the edge-shard path.
    monkeypatch.setenv("GOLDENMATCH_FS_SPILL_FORCE_SHARD", "1")
    shard = run_fs_dedupe_spill(df, blocking, mk, em, cfg, str(tmp_path / "shard"))
    assert shard["spill"] is True
    assert shard["fused"] is False  # the shard/external-WCC path actually ran
    assert shard["pairs"] is not None and shard["pairs"] >= 1  # real edges spilled

    for k in ("unique_count", "dupes_count", "golden_count"):
        assert fused[k] == shard[k], k
    assert (
        _partition_set_from_parquet(fused["dupes_path"])
        == _partition_set_from_parquet(shard["dupes_path"])
    )


def test_spill_and_sequential_orchestrators_agree(tmp_path):
    """run_fs_dedupe_spill and run_fs_dedupe_sequential write the SAME
    unique/dupes/golden counts. Both score via score_buckets_arrow over the SAME
    edge set; the spill path streams those edges through disk shards + external
    union-find (partition-exact with _cluster_arrow_native), the sequential path
    clusters in RAM — so the output-row counts must match by construction."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        run_fs_dedupe_sequential,
        run_fs_dedupe_spill,
    )

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    seq = run_fs_dedupe_sequential(df, blocking, mk, em, cfg, str(tmp_path / "seq"))
    spill = run_fs_dedupe_spill(df, blocking, mk, em, cfg, str(tmp_path / "spill"))
    for k in ("unique_count", "dupes_count", "golden_count"):
        assert seq[k] == spill[k], k

    # Same records land in the dupes partition (not just the same counts).
    seq_parts = _partition_set_from_parquet(seq["dupes_path"])
    spill_parts = _partition_set_from_parquet(spill["dupes_path"])
    assert seq_parts == spill_parts


def test_end_to_end_bucketed_dedupe(tmp_path):
    """run_fs_dedupe_bucketed: frame -> per-pass hash-bucket shards on disk ->
    per-bucket score + edge spill -> external WCC -> streamed parquet. Rows
    preserved, planted dups found, a golden per multi-member cluster, bucketed=True
    (never the whole-frame partition / fused kernel)."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_bucketed

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    res = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path))

    assert res["bucketed"] is True
    assert res["streaming"] is True
    assert res["fused"] is False  # bucketed never uses the frame-gathering fused kernel
    assert res["pairs"] is not None  # real edges spilled through the shard path
    assert res["unique_count"] + res["dupes_count"] == df.height
    assert res["dupes_count"] >= 2
    assert res["golden_count"] >= 1


def test_bucketed_and_sequential_orchestrators_agree(tmp_path):
    """run_fs_dedupe_bucketed writes the SAME unique/dupes/golden counts AND the
    SAME dupes-partition as run_fs_dedupe_sequential. Hash-bucketing by the block
    key co-locates each block wholly in one bucket, so per-bucket scoring is
    partition-exact with whole-frame scoring (single static pass)."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        run_fs_dedupe_bucketed,
        run_fs_dedupe_sequential,
    )

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    seq = run_fs_dedupe_sequential(df, blocking, mk, em, cfg, str(tmp_path / "seq"))
    buk = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "buk"))
    for k in ("unique_count", "dupes_count", "golden_count"):
        assert seq[k] == buk[k], k
    assert (
        _partition_set_from_parquet(seq["dupes_path"])
        == _partition_set_from_parquet(buk["dupes_path"])
    )


def test_bucketed_multipass_agrees_with_sequential(tmp_path):
    """The multi-pass-specific risk: per-pass hash-bucketing (bucket the frame once
    per blocking pass on that pass's key) must reproduce the exact multi_pass edge
    set. A dup shares BOTH block keys; each pass is bucketed independently and the
    per-pass edges union — partition-exact with the whole-frame reference."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        run_fs_dedupe_bucketed,
        run_fs_dedupe_sequential,
    )
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    rows = []
    fam = [
        ("John", "Smith", "90210", "AA"), ("Jon", "Smith", "90210", "AA"),
        ("Jane", "Doe", "10001", "BB"), ("Janet", "Doe", "10001", "BB"),
        ("Bob", "Jones", "60601", "CC"), ("Robert", "Jones", "60601", "CC"),
        ("Alice", "Brown", "30301", "DD"), ("Alicia", "Brown", "30301", "DD"),
        ("Amy", "Clark", "90210", "EE"), ("Amie", "Clark", "90210", "EE"),
    ]
    for rid, (fn, ln, zp, cc) in enumerate(fam, start=1):
        rows.append({"__row_id__": rid, "first_name": fn, "last_name": ln,
                     "zip": zp, "city_code": cc})
    df = pl.DataFrame(rows)
    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2,
                          partial_threshold=0.85),
        ],
    )
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=["zip"]),
                BlockingKeyConfig(fields=["city_code"])],
    )
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    seq = run_fs_dedupe_sequential(df, blocking, mk, em, cfg, str(tmp_path / "seq"))
    buk = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "buk"))
    for k in ("unique_count", "dupes_count", "golden_count"):
        assert seq[k] == buk[k], k
    assert (
        _partition_set_from_parquet(seq["dupes_path"])
        == _partition_set_from_parquet(buk["dupes_path"])
    )


def _assert_bucketed_outputs_identical(a, b):
    """Both result dicts carry byte-identical unique/dupes/golden parquet — counts
    AND full row contents (order-insensitive) — plus the same pair count. The
    streaming-prep on-disk base must produce EXACTLY the resident-base output."""
    import polars as pl

    for k in ("unique_count", "dupes_count", "golden_count", "pairs"):
        assert a[k] == b[k], k
    for key in ("unique_path", "dupes_path", "golden_path"):
        pa_, pb_ = a[key], b[key]
        assert (pa_ is None) == (pb_ is None), key
        if pa_ is None:
            continue
        da, db = pl.read_parquet(pa_), pl.read_parquet(pb_)
        assert da.columns == db.columns, key
        assert da.sort(by=da.columns).equals(db.sort(by=db.columns)), key


def test_bucketed_prep_stream_flag_matches_resident(tmp_path, monkeypatch):
    """GOLDENMATCH_FS_PREP_STREAM=1 spills the prepared base to an on-disk Arrow-IPC
    file (read back memory-mapped batch-by-batch) instead of holding it resident; the
    streamed unique/dupes/golden parquet must be byte-identical to the flag-OFF
    resident path. Multi-pass so both the bucketing scan AND the output scan read the
    base off disk."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_bucketed

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=["zip"]),
                BlockingKeyConfig(fields=["last_name"])],
    )
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_PREP_STREAM", "0")
    off = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "off"))
    monkeypatch.setenv("GOLDENMATCH_FS_PREP_STREAM", "1")
    on = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "on"))

    assert off["bucketed"] is True and on["bucketed"] is True
    _assert_bucketed_outputs_identical(off, on)


def test_bucketed_accepts_ipc_path_base(tmp_path, monkeypatch):
    """Passing an on-disk Arrow-IPC PATH as ``prepared_df`` (the batched-ingest path
    hands a disk-backed base) yields identical output to passing the resident
    pa.Table — the path-aware base seam is transparent to the route."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        _write_base_ipc,
        run_fs_dedupe_bucketed,
    )

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.delenv("GOLDENMATCH_FS_PREP_STREAM", raising=False)
    resident = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "res"))

    # Spill the same prepared frame to an Arrow-IPC file and pass the PATH directly.
    base_path = str(tmp_path / "base.arrow")
    _write_base_ipc(df.to_arrow(), base_path)
    viapath = run_fs_dedupe_bucketed(
        base_path, blocking, mk, em, cfg, str(tmp_path / "path")
    )

    _assert_bucketed_outputs_identical(resident, viapath)


def _output_dict_and_sets(res, tmp_dir):
    """(counts, unique-rowid-set, dupes-partition-set, golden-rowcount) for an
    FS output result dict -- the parity surface for comparing the batched output
    against the resident-join reference."""
    import polars as pl

    counts = (res["unique_count"], res["dupes_count"], res["golden_count"])
    uniq = set(pl.read_parquet(res["unique_path"])["__row_id__"].to_list())
    dupes = _partition_set_from_parquet(res["dupes_path"])
    gcount = (
        pl.read_parquet(res["golden_path"]).height
        if res["golden_path"] is not None
        else 0
    )
    return counts, uniq, dupes, gcount


@pytest.mark.parametrize("gapped", [False, True])
def test_batched_output_equals_resident_join(tmp_path, gapped):
    """`_stream_fs_dedupe_output_batched` (join-free, Phase 3a) produces the SAME
    unique/dupes/golden as the resident `_stream_fs_dedupe_output_arrow` join, for
    BOTH a contiguous `__row_id__` (lo!=0 fast path -- scatter, not slice) and a
    GAPPED `__row_id__` (the per-batch hash-join fallback). Also locks `__xform_*`
    exclusion. Tiny batch_rows forces multi-batch streaming incl. a cluster whose
    members straddle batches (golden built once)."""
    import types

    import pyarrow as pa
    from goldenmatch.backends.fs_out_of_core import (
        _stream_fs_dedupe_output_arrow,
        _stream_fs_dedupe_output_batched,
    )

    # Row ids: contiguous {2..6} (lo=2, not 0) or gapped {2,3,7,8,11}.
    rids = [2, 3, 7, 8, 11] if gapped else [2, 3, 4, 5, 6]
    frame = pa.table(
        {
            "__row_id__": pa.array(rids, pa.int64()),
            "first_name": ["John", "Jon", "solo", "Amy", "Amie"],
            "last_name": ["Smith", "Smith", "Nemo", "Clark", "Clark"],
            # A transform column that MUST be excluded from output record_cols.
            "__xform_x__": ["a", "b", "c", "d", "e"],
        }
    )
    # Clusters: (r0,r1)->0 [multi], (r2)->1 [singleton], (r3,r4)->2 [multi].
    assignments = pa.table(
        {
            "__row_id__": pa.array(rids, pa.int64()),
            "__cluster_id__": pa.array([0, 0, 1, 2, 2], pa.int64()),
        }
    )
    cfg = types.SimpleNamespace(golden_rules=None)

    ref = _stream_fs_dedupe_output_arrow(
        frame, assignments, cfg, str(tmp_path / "ref")
    )
    # batch_rows=2 -> the (r3,r4) cluster and (r0,r1) cluster each straddle batches.
    got = _stream_fs_dedupe_output_batched(
        frame, assignments, cfg, str(tmp_path / "got"), batch_rows=2
    )

    assert _output_dict_and_sets(got, tmp_path / "got") == _output_dict_and_sets(
        ref, tmp_path / "ref"
    )
    # Concrete: 1 singleton, 4 duped rows, 2 golden; __xform_ excluded.
    assert got["unique_count"] == 1 and got["dupes_count"] == 4
    assert got["golden_count"] == 2
    import polars as pl

    assert "__xform_x__" not in pl.read_parquet(got["dupes_path"]).columns


def _partition_from_assignments(asn):
    """Cluster partition (frozenset of frozensets of row_ids) from an
    {__row_id__, __cluster_id__} assignments table -- cluster-id VALUES are
    irrelevant, only the grouping is."""
    import collections

    rid = asn.column("__row_id__").to_pylist()
    cid = asn.column("__cluster_id__").to_pylist()
    groups = collections.defaultdict(set)
    for r, c in zip(rid, cid):
        groups[c].add(r)
    return frozenset(frozenset(g) for g in groups.values())


def test_external_wcc_array_and_dict_agree():
    """The numpy array-UF (contiguous ids) and the dict-UF fallback (gapped ids)
    produce the SAME partition on the same logical edge set. Locks the array-UF
    rewrite against the reference dict path."""
    import tempfile

    import pyarrow as pa
    from goldenmatch.backends.fs_out_of_core import (
        _external_wcc_dict,
        external_wcc_from_shards,
        spill_pair_shard,
    )

    # Edges over contiguous ids {0..9}: components {0,1,2},{3,4},{5},{6,7,8,9}...
    edges = [(0, 1), (1, 2), (3, 4), (6, 7), (7, 8), (8, 9)]
    tbl = pa.table({
        "id_a": pa.array([a for a, _ in edges], pa.int64()),
        "id_b": pa.array([b for _, b in edges], pa.int64()),
        "score": pa.array([0.9] * len(edges), pa.float64()),
    })
    d = tempfile.mkdtemp()
    shard = spill_pair_shard(tbl, d, 0)

    all_ids = range(0, 10)  # contiguous -> array-UF fast path
    asn_arr, n_arr = external_wcc_from_shards([shard], all_ids, 100, None)
    asn_dict, n_dict = _external_wcc_dict([shard], list(all_ids), None)

    assert n_arr == n_dict == len(edges)
    assert asn_arr.num_rows == asn_dict.num_rows == 10
    assert _partition_from_assignments(asn_arr) == _partition_from_assignments(asn_dict)
    # Singletons (5) present as their own cluster.
    assert frozenset([5]) in _partition_from_assignments(asn_arr)
    # Threshold filter drops the sub-cut edge on BOTH paths identically.
    tbl2 = pa.table({
        "id_a": pa.array([0, 3], pa.int64()), "id_b": pa.array([1, 4], pa.int64()),
        "score": pa.array([0.9, 0.2], pa.float64()),
    })
    shard2 = spill_pair_shard(tbl2, d, 1)
    a_arr, _ = external_wcc_from_shards([shard2], range(0, 5), 100, 0.5)
    a_dict, _ = _external_wcc_dict([shard2], list(range(0, 5)), 0.5)
    assert _partition_from_assignments(a_arr) == _partition_from_assignments(a_dict)


def test_bucketed_parallel_scoring_parity(tmp_path, monkeypatch):
    """Concurrent shard scoring (workers>1) yields byte-identical output to the
    serial path (workers=1) -- the external WCC is invariant to edge/shard order,
    so parallelism changes only the wall."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_bucketed

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=["zip"]),
                BlockingKeyConfig(fields=["last_name"])],
    )
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_SCORE_WORKERS", "1")
    serial = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "s"))
    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_SCORE_WORKERS", "4")
    par = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "p"))

    for k in ("unique_count", "dupes_count", "golden_count"):
        assert serial[k] == par[k], k
    assert (
        _partition_set_from_parquet(serial["dupes_path"])
        == _partition_set_from_parquet(par["dupes_path"])
    )


def test_bucketed_golden_chunked_write_parity(tmp_path, monkeypatch):
    """The golden build+write is chunked by cluster (`__cluster_id__ % n`) to
    bound the per-cluster survivorship transient at scale (the 50M OOM lever).
    Forcing MANY chunks (tiny threshold) must produce byte-identical golden to the
    single-chunk build -- clusters are independent, so chunk assignment/order is
    irrelevant, and `% n` keeps every cluster's rows in ONE chunk (never split)."""
    import types

    import polars as pl
    from goldenmatch.backends import fs_out_of_core as F
    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_bucketed

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setattr(F, "_GOLDEN_BUILD_CHUNK_ROWS", 10**9)  # single chunk
    one = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "one"))
    monkeypatch.setattr(F, "_GOLDEN_BUILD_CHUNK_ROWS", 1)  # one cluster per chunk
    many = run_fs_dedupe_bucketed(df, blocking, mk, em, cfg, str(tmp_path / "many"))

    assert one["golden_count"] == many["golden_count"] >= 1
    g1 = pl.read_parquet(one["golden_path"])
    g2 = pl.read_parquet(many["golden_path"])
    assert g1.sort(by=g1.columns).equals(g2.sort(by=g2.columns))


def test_batched_output_empty_golden_unlinks_stale(tmp_path):
    """When a run yields no golden (all singletons or all oversized), the batched
    output unlinks a prior-run golden.parquet and returns golden_path=None --
    matching the resident function's empty-golden contract."""
    import types

    import pyarrow as pa
    from goldenmatch.backends.fs_out_of_core import _stream_fs_dedupe_output_batched

    out = tmp_path / "out"
    out.mkdir()
    stale = out / "golden.parquet"
    stale.write_bytes(b"stale")  # a prior run's file

    frame = pa.table(
        {"__row_id__": pa.array([1, 2, 3], pa.int64()), "v": ["a", "b", "c"]}
    )
    # All singletons -> no golden.
    assignments = pa.table(
        {
            "__row_id__": pa.array([1, 2, 3], pa.int64()),
            "__cluster_id__": pa.array([0, 1, 2], pa.int64()),
        }
    )
    res = _stream_fs_dedupe_output_batched(
        frame, assignments, types.SimpleNamespace(golden_rules=None), str(out)
    )
    assert res["golden_count"] == 0
    assert res["golden_path"] is None
    assert not stale.exists()  # stale golden.parquet removed


def test_fs_streaming_route_selects_orchestrator(monkeypatch):
    from goldenmatch.backends.fs_out_of_core import fs_streaming_route

    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)
    for src, expected in (
        ("sequential", "sequential"), ("spill", "spill"),
        ("bucketed", "bucketed"), ("duckdb", "duckdb"),
        ("frame", None), ("eager", None), ("auto", None),
    ):
        monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", src)
        assert fs_streaming_route() == expected


def test_dedupe_to_parquet_sequential_parity_with_in_memory(tmp_path, monkeypatch):
    """dedupe_to_parquet under GOLDENMATCH_FS_BLOCK_SOURCE=sequential partitions
    the SAME records as the default in-memory FS route."""
    import polars as pl
    from goldenmatch import dedupe_df, dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)

    csv_path = tmp_path / "people.csv"
    _make_person_csv(csv_path)
    df = pl.read_csv(csv_path)
    cfg = _fs_person_config()

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "eager")
    mem = dedupe_df(df, config=cfg)
    mem_parts = _partitions(mem)

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "sequential")
    out_dir = tmp_path / "out"
    res = dedupe_to_parquet(str(csv_path), out_dir=str(out_dir), config=cfg)
    assert res["streaming"] is True
    assert res["sequential"] is True
    stream_parts = _partition_set_from_parquet(res["dupes_path"])

    assert stream_parts == sorted(mem_parts)


def test_dedupe_to_parquet_spill_parity_with_in_memory(tmp_path, monkeypatch):
    """dedupe_to_parquet under GOLDENMATCH_FS_BLOCK_SOURCE=spill (the DuckDB-free
    per-pass-shard + external-WCC route) partitions the SAME records as the default
    in-memory FS route — end-to-end through the pipeline dispatch."""
    import polars as pl
    from goldenmatch import dedupe_df, dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)

    csv_path = tmp_path / "people.csv"
    _make_person_csv(csv_path)
    df = pl.read_csv(csv_path)
    cfg = _fs_person_config()

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "eager")
    mem = dedupe_df(df, config=cfg)
    mem_parts = _partitions(mem)

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "spill")
    out_dir = tmp_path / "out"
    res = dedupe_to_parquet(str(csv_path), out_dir=str(out_dir), config=cfg)
    assert res["streaming"] is True
    assert res["spill"] is True
    stream_parts = _partition_set_from_parquet(res["dupes_path"])

    assert stream_parts == sorted(mem_parts)


def test_dedupe_to_parquet_bucketed_parity_with_in_memory(tmp_path, monkeypatch):
    """dedupe_to_parquet under GOLDENMATCH_FS_BLOCK_SOURCE=bucketed (the
    frame-residency per-pass-disk-bucketing route) partitions the SAME records as
    the default in-memory FS route — end-to-end through the pipeline dispatch."""
    import polars as pl
    from goldenmatch import dedupe_df, dedupe_to_parquet

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    monkeypatch.delenv("GOLDENMATCH_FS_OUT_OF_CORE", raising=False)

    csv_path = tmp_path / "people.csv"
    _make_person_csv(csv_path)
    df = pl.read_csv(csv_path)
    cfg = _fs_person_config()

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "eager")
    mem = dedupe_df(df, config=cfg)
    mem_parts = _partitions(mem)

    monkeypatch.setenv("GOLDENMATCH_FS_BLOCK_SOURCE", "bucketed")
    out_dir = tmp_path / "out"
    res = dedupe_to_parquet(str(csv_path), out_dir=str(out_dir), config=cfg)
    assert res["streaming"] is True
    assert res["bucketed"] is True
    stream_parts = _partition_set_from_parquet(res["dupes_path"])

    assert stream_parts == sorted(mem_parts)


# ── external WCC over spilled edge shards (DuckDB-free bounded-edge clustering) ──
# Spec: docs/superpowers/specs/2026-08-02-fs-duckdb-free-spill-external-wcc-design.md


def _ref_partition(pairs, all_ids, link_threshold):
    """Reference partition: a whole-set union-find over the threshold-filtered edge
    list + singleton fold. The ground truth external_wcc_from_shards must match."""
    parent = {i: i for i in all_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, s in pairs:
        if link_threshold is not None and s < link_threshold:
            continue
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps: dict[int, set] = {}
    for rid in all_ids:
        comps.setdefault(find(rid), set()).add(rid)
    return {frozenset(v) for v in comps.values()}


def _asn_partition(asn):
    """(__row_id__, __cluster_id__) pa.Table -> {frozenset(row_ids) per cluster}."""
    groups: dict[int, set] = {}
    for r, c in zip(
        asn.column("__row_id__").to_pylist(), asn.column("__cluster_id__").to_pylist()
    ):
        groups.setdefault(c, set()).add(r)
    return {frozenset(v) for v in groups.values()}


def _spill_pairs(pairs, shard_dir, n_shards):
    """Split a pair list into n_shards Arrow IPC shards (a pair may straddle shards)."""
    from goldenmatch.backends.fs_out_of_core import spill_pair_shard
    from goldenmatch.backends.score_buckets import pairs_to_pair_stream

    per = max(1, (len(pairs) + n_shards - 1) // n_shards)
    paths = []
    for i in range(0, len(pairs), per):
        tbl = pairs_to_pair_stream(pairs[i : i + per])
        paths.append(spill_pair_shard(tbl, shard_dir, len(paths)))
    return paths


def test_external_wcc_matches_reference_across_shards(tmp_path):
    """external_wcc_from_shards streams edges from disk one shard at a time and
    produces the SAME component partition a whole-set union-find would — including a
    chain split across shards and singletons folded from all_ids."""
    from goldenmatch.backends.fs_out_of_core import external_wcc_from_shards

    # 0-1-2 chain, 3-4 pair, 5/6 singletons; edges deliberately out of order.
    pairs = [(2, 1, 0.9), (0, 1, 0.8), (3, 4, 0.95)]
    all_ids = list(range(7))
    paths = _spill_pairs(pairs, str(tmp_path), n_shards=3)  # chain crosses shards

    asn, n_pairs = external_wcc_from_shards(paths, all_ids, 100, None)
    assert n_pairs == 3
    assert _asn_partition(asn) == _ref_partition(pairs, all_ids, None)
    # Every row assigned exactly once.
    assert sorted(asn.column("__row_id__").to_pylist()) == all_ids


def test_external_wcc_threshold_uses_best_cross_shard_score(tmp_path):
    """A pair emitted twice across shards with scores straddling the cut unions iff
    its BEST occurrence clears the cut (max is monotone) — the per-edge streaming
    filter equals filter-after-max-dedup."""
    from goldenmatch.backends.fs_out_of_core import external_wcc_from_shards

    # (0,1) appears at 0.4 (shard A) and 0.7 (shard B); (2,3) only at 0.4.
    pairs = [(0, 1, 0.4), (2, 3, 0.4), (0, 1, 0.7)]
    all_ids = list(range(4))
    paths = _spill_pairs(pairs, str(tmp_path), n_shards=3)

    asn, _ = external_wcc_from_shards(paths, all_ids, 100, 0.5)
    part = _asn_partition(asn)
    assert frozenset({0, 1}) in part          # best score 0.7 >= 0.5 -> linked
    assert frozenset({2}) in part and frozenset({3}) in part  # 0.4 < 0.5 -> split
    assert part == _ref_partition(pairs, all_ids, 0.5)


def test_external_wcc_partition_parity_with_cluster_arrow_native(tmp_path):
    """The disk-spill external WCC yields the SAME partition as the in-RAM
    _cluster_arrow_native on the identical pair set (cluster-id VALUES may differ;
    the PARTITION does not)."""
    from goldenmatch.backends.fs_out_of_core import (
        _cluster_arrow_native,
        external_wcc_from_shards,
    )
    from goldenmatch.backends.score_buckets import pairs_to_pair_stream

    pairs = [
        (0, 1, 0.9), (1, 2, 0.85), (3, 4, 0.6), (4, 5, 0.7),
        (6, 7, 0.55), (0, 2, 0.95),  # redundant edge (already connected)
    ]
    all_ids = list(range(9))  # 8 is a singleton
    link_threshold = 0.5

    ref_asn, _ = _cluster_arrow_native(
        all_ids, pairs_to_pair_stream(pairs), 100, link_threshold
    )
    paths = _spill_pairs(pairs, str(tmp_path), n_shards=4)
    got_asn, _ = external_wcc_from_shards(paths, all_ids, 100, link_threshold)

    assert _asn_partition(got_asn) == _asn_partition(ref_asn)
