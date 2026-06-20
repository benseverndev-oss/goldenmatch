import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inject_dups


def _base(n=100):
    return [(f"b{i}", f"document number {i} " + "filler words here " * 20) for i in range(n)]


def test_determinism(tmp_path):
    out1, t1 = inject_dups.build(_base(), seed=0, frac=0.3, out_dir=tmp_path / "a")
    out2, t2 = inject_dups.build(_base(), seed=0, frac=0.3, out_dir=tmp_path / "b")
    assert pl.read_parquet(out1).equals(pl.read_parquet(out2))
    assert pl.read_parquet(t1).equals(pl.read_parquet(t2))


def test_truth_columns_and_dup_membership(tmp_path):
    out, truth = inject_dups.build(_base(100), seed=0, frac=0.3, out_dir=tmp_path)
    corpus = pl.read_parquet(out)
    tr = pl.read_parquet(truth)
    assert set(corpus.columns) == {"doc_id", "text"}
    assert set(tr.columns) == {"record_id", "cluster_id"}
    # every corpus doc has exactly one truth row
    assert tr.height == corpus.height
    assert set(tr["record_id"]) == set(corpus["doc_id"])
    # at least one non-singleton cluster exists (dups were injected)
    sizes = tr.group_by("cluster_id").len()
    assert sizes["len"].max() >= 2
    # injected dups exist and each maps to its source's cluster
    dup_rows = corpus.filter(pl.col("doc_id").str.contains("~dup"))
    assert dup_rows.height > 0
    tr_map = dict(zip(tr["record_id"].to_list(), tr["cluster_id"].to_list()))
    for did in dup_rows["doc_id"].to_list():
        src = did.split("~dup")[0]
        assert tr_map[did] == tr_map[src]


def test_modes_present(tmp_path):
    out, _ = inject_dups.build(_base(200), seed=1, frac=0.5, out_dir=tmp_path)
    ids = pl.read_parquet(out)["doc_id"].to_list()
    # dup ids encode their mode: ...~dup-exact / ~dup-partial / ~dup-paraphrase
    assert any("exact" in i for i in ids)
    assert any("partial" in i for i in ids)
    assert any("paraphrase" in i for i in ids)
