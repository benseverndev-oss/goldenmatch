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


def test_streaming_golden_batching_parity(tmp_path, monkeypatch):
    """The cluster-batched golden build (GOLDENMATCH_FS_OOC_GOLDEN_BATCH) emits the
    SAME golden/unique/dupes counts whether every golden cluster is its OWN batch
    (=1, exercises the multi-batch ParquetWriter path) or all in one — the
    batching bounds golden-stage memory without changing the output."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_OOC_GOLDEN_BATCH", "1")  # one cluster/batch
    res_batched = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path / "b"))
    monkeypatch.setenv("GOLDENMATCH_FS_OOC_GOLDEN_BATCH", "1000000")  # single shot
    res_single = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path / "s"))

    assert res_batched["golden_count"] >= 1
    for k in ("unique_count", "dupes_count", "golden_count"):
        assert res_batched[k] == res_single[k], k
    # The multi-batch write must produce a valid parquet whose rows == the count.
    import pyarrow.parquet as pq
    assert pq.read_metadata(res_batched["golden_path"]).num_rows == res_batched["golden_count"]


def test_streaming_frees_prepared_frame_via_holder(tmp_path):
    """run_fs_dedupe_streaming([frame]) drops the frame after the DuckDB load: the
    holder is nulled, so the ~1x prepared frame is not resident through clustering
    + output (the scale lever). A weakref to the frame must die after the call."""
    import gc
    import types
    import weakref

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    ref = weakref.ref(df)
    holder = [df]
    del df  # holder is now the only strong reference
    res = run_fs_dedupe_streaming(holder, blocking, mk, em, cfg, str(tmp_path))
    assert holder[0] is None            # scorer nulled the holder slot after load
    gc.collect()
    assert ref() is None                # the prepared frame was freed
    assert res["unique_count"] + res["dupes_count"] >= 1


def test_streaming_bare_frame_still_supported(tmp_path):
    """A bare-frame caller (holder=None path) still works unchanged — direct
    callers / tests never build a holder."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    res = run_fs_dedupe_streaming(df, blocking, mk, em, cfg, str(tmp_path))
    assert res["unique_count"] + res["dupes_count"] == df.height


def _stream_partitions(res_dir):
    """Sorted dupes row_id partitions for a run_fs_dedupe_streaming result dir —
    the cluster-outcome fingerprint."""
    import os

    dupes = os.path.join(res_dir, "dupes.parquet")
    return _partition_set_from_parquet(dupes) if os.path.exists(dupes) else []


@pytest.mark.parametrize("link_threshold", [None, 0.5])
def test_streaming_pair_spill_parity_with_in_ram(tmp_path, monkeypatch, link_threshold):
    """The DuckDB pair-STREAM spill (GOLDENMATCH_FS_OOC_SPILL_PAIRS=1, default) — max
    -score canonical dedup + best-score link filter done in DuckDB SQL — yields the
    SAME cluster outcome as the in-RAM arrow path (Rust dedup_pairs_arrow kernel),
    with and without a link threshold. Locks the SQL-dedup == arrow-kernel-dedup
    equivalence."""
    import types

    from goldenmatch.backends.fs_out_of_core import run_fs_dedupe_streaming

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_OOC_SPILL_PAIRS", "0")  # in-RAM arrow oracle
    res_ram = run_fs_dedupe_streaming(
        df, blocking, mk, em, cfg, str(tmp_path / "ram"), link_threshold=link_threshold
    )
    monkeypatch.setenv("GOLDENMATCH_FS_OOC_SPILL_PAIRS", "1")  # DuckDB spill
    res_spill = run_fs_dedupe_streaming(
        df, blocking, mk, em, cfg, str(tmp_path / "spill"), link_threshold=link_threshold
    )

    for k in ("unique_count", "dupes_count", "golden_count", "pairs"):
        assert res_ram[k] == res_spill[k], (k, res_ram[k], res_spill[k])
    assert _stream_partitions(str(tmp_path / "ram")) == _stream_partitions(str(tmp_path / "spill"))


@pytest.mark.parametrize("link_threshold", [None, 0.5])
def test_streaming_union_find_cluster_parity(tmp_path, monkeypatch, link_threshold):
    """The Rust streaming Union-Find clusterer (GOLDENMATCH_FS_OOC_STREAM_CLUSTER=1)
    yields the SAME cluster partitions as the one-shot arrow kernel (=0), with and
    without a link threshold — the memory-bounded path is partition-parity-safe."""
    import types

    from goldenmatch.backends.fs_out_of_core import (
        _streaming_cluster_symbol_available,
        run_fs_dedupe_streaming,
    )

    if not _streaming_cluster_symbol_available():
        pytest.skip("native StreamingClusterBuilder not available")

    df = _bigger_df()
    mk = _make_probabilistic_mk()
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    em = _train(df, blocking, mk)
    cfg = types.SimpleNamespace(golden_rules=None)

    monkeypatch.setenv("GOLDENMATCH_FS_OOC_STREAM_CLUSTER", "0")  # one-shot oracle
    res_oneshot = run_fs_dedupe_streaming(
        df, blocking, mk, em, cfg, str(tmp_path / "o"), link_threshold=link_threshold
    )
    monkeypatch.setenv("GOLDENMATCH_FS_OOC_STREAM_CLUSTER", "1")  # streaming UF
    res_stream = run_fs_dedupe_streaming(
        df, blocking, mk, em, cfg, str(tmp_path / "s"), link_threshold=link_threshold
    )

    for k in ("unique_count", "dupes_count", "golden_count", "pairs"):
        assert res_oneshot[k] == res_stream[k], (k, res_oneshot[k], res_stream[k])
    assert _stream_partitions(str(tmp_path / "o")) == _stream_partitions(str(tmp_path / "s"))
