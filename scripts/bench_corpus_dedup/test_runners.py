import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

HERE = Path(__file__).resolve().parent


def _load(mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, HERE / f"{mod_name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mod)
    return mod


def _make_corpus(tmp_path):
    # Use the real (diverse) offline corpus, like the gate + smoke test. Synthetic
    # near-identical text collapses into one giant block and wedges the auto-config
    # controller's sample pipeline — a fixture pathology, not a tier bug.
    inj = _load("inject_dups")
    corpora = _load("corpora")
    base = list(corpora.load_corpus("offline", n_docs=60, seed=0))
    return inj.build(base, seed=0, frac=0.4, out_dir=tmp_path)


def _env():
    return {**os.environ, "PYTHONPATH": "packages/python/goldenmatch",
            "GOLDENMATCH_NATIVE": "0", "POLARS_SKIP_CPU_CHECK": "1",
            "PYTHONIOENCODING": "utf-8"}


def test_goldenmatch_runner_engages_tier(tmp_path):
    corpus, _truth = _make_corpus(tmp_path)
    out = tmp_path / "gm.json"
    pred = tmp_path / "gm.pred.parquet"
    rc = subprocess.run(
        [sys.executable, str(HERE / "run_goldenmatch.py"),
         "--input", str(corpus), "--out", str(out), "--pred-out", str(pred),
         "--recall-target", "0.95"],
        env=_env(),
    ).returncode
    assert rc == 0, "runner exited non-zero"
    r = json.loads(out.read_text())
    assert r["status"] == "ok", r
    assert r["verify_mode"] == "sketch_distance"        # tier engaged
    assert r["blocking_strategy"] in ("lsh", "simhash")
    assert r["candidate_pairs"] is not None
    assert r["docs_per_sec"] and r["mb_per_sec"]
    assert 0.0 <= r["reduction_ratio"] <= 1.0
    p = pl.read_parquet(pred)
    assert set(p.columns) == {"record_id", "pred_cluster_id"}
    # ids must JOIN to the corpus doc_ids — guards the __row_id__->doc_id remap.
    corpus_ids = set(pl.read_parquet(corpus)["doc_id"].to_list())
    assert set(p["record_id"].to_list()) <= corpus_ids and len(p) > 0


_HAS_DATATROVE = importlib.util.find_spec("datatrove") is not None


def test_datatrove_dup_edge_parsing(tmp_path):
    """Parse datatrove buckets `.dups` output the way the runner must (#1150).

    The recall=0 bug read the CLUSTER stage's `.remove` list (single doc ids, no
    pairs). The edges live in the BUCKETS `.dups` files as packed `<4I` records
    (file_id_1, doc_id_1, file_id_2, doc_id_2). This synthesizes that exact byte
    format — no datatrove install needed — and checks edges + de-dup + SENTINEL skip.
    """
    import struct

    dt = _load("run_datatrove")
    buck = tmp_path / "buckets"
    buck.mkdir()
    SENT = (1 << 32) - 1
    records = [
        (0, 1, 0, 3),      # edge 1-3
        (0, 3, 0, 7),      # edge 3-7  (1,3,7 form one cluster)
        (0, 7, 0, 3),      # same pair as 3-7, reversed -> de-duped
        (0, 5, 0, 9),      # edge 5-9  (separate cluster)
        (SENT, SENT, 0, 4),  # index-signature match -> skipped
        (0, 2, 0, 2),      # self-pair -> skipped
    ]
    blob = b"".join(struct.pack("<4I", *r) for r in records)
    (buck / "00000_00.dups").write_bytes(blob)

    edges, raw = dt._read_dup_edges(buck)
    assert raw == 6  # every record counted, including skipped ones
    assert edges == [(1, 3), (3, 7), (5, 9)]  # de-duped, canonical, no self/SENTINEL

    # Union-find the parsed edges the way _run_minhash does: {1,3,7} and {5,9}.
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[min(ra, rb)] = max(ra, rb)
    assert find(1) == find(3) == find(7)
    assert find(5) == find(9)
    assert find(1) != find(5)


def test_datatrove_dup_edges_empty_when_no_dups(tmp_path):
    dt = _load("run_datatrove")
    buck = tmp_path / "buckets"
    buck.mkdir()
    edges, raw = dt._read_dup_edges(buck)
    assert edges == [] and raw == 0


@pytest.mark.skipif(not _HAS_DATATROVE, reason="datatrove not installed (headline lane installs it)")
def test_datatrove_runner_schema(tmp_path):
    corpus, _ = _make_corpus(tmp_path)
    out = tmp_path / "dt.json"
    pred = tmp_path / "dt.pred.parquet"
    rc = subprocess.run(
        [sys.executable, str(HERE / "run_datatrove.py"),
         "--input", str(corpus), "--out", str(out), "--pred-out", str(pred)],
        env=_env(),
    ).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["engine"] == "datatrove"
    assert r["status"] in ("ok", "OOM", "error")
    if r["status"] == "ok":
        assert r["docs_per_sec"] and r["mb_per_sec"] and r["candidate_pairs"] is not None
        assert set(pl.read_parquet(pred).columns) == {"record_id", "pred_cluster_id"}
