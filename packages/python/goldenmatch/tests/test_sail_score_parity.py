"""S1 gate: the Sail score/dedup pipeline's emitted pair SET matches a
python-rapidfuzz reference. Self-contained (no datafusion). Skips where the
sail extra is absent; runs in the `sail` CI lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")
pytest.importorskip("polars")


@pytest.fixture(scope="module")
def spark():
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def _fixture_rows():
    """Mirror the one-box parity fixture: dense / 2-member / 3-chain /
    singletons on last_name, block on zip. Returns list of (row_id, last, zip).
    All within-block strings are identical -> every pair scores 1.0 (0.15
    margin over the 0.85 threshold), so no pair is near-threshold."""
    last = (["Aaaa"] * 5 + ["Brown", "Brown"]
            + ["Carter", "Carter", "Carter"] + ["Dixon", "Ellis"])
    zips = (["10001"] * 5 + ["20002"] * 2 + ["30003"] * 3 + ["40004", "50005"])
    return [(i, last[i], zips[i]) for i in range(len(last))]


def test_sail_scorer_udf_matches_rapidfuzz(spark):
    from goldenmatch.sail.scorers import make_scorer_udf
    from rapidfuzz.distance import JaroWinkler

    df = spark.createDataFrame([("Aaaa", "Aaaa"), ("Brown", "Browne")], ["a", "b"])
    udf = make_scorer_udf("jaro_winkler")
    got = {
        (r["a"], r["b"]): r["s"]
        for r in df.select("a", "b", udf("a", "b").alias("s")).collect()
    }
    for (a, b), s in got.items():
        assert abs(s - JaroWinkler.normalized_similarity(a, b)) < 1e-9


def _reference_pairs(rows, threshold):
    """python-rapidfuzz brute-force within block (mirror _inmemory_comparand):
    canonical (min, max) above-threshold pairs. The SAME rapidfuzz the Sail UDF
    uses -> exact set parity is the gate."""
    from collections import defaultdict

    from rapidfuzz.distance import JaroWinkler

    by_block = defaultdict(list)
    for rid, last, z in rows:
        by_block[z].append((rid, last))
    out = set()
    for members in by_block.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (ida, va), (idb, vb) = members[i], members[j]
                if JaroWinkler.normalized_similarity(va, vb) >= threshold:
                    out.add((min(ida, idb), max(ida, idb)))
    return out


def test_sail_score_dedup_pair_set_parity(spark):
    from goldenmatch.sail.scoring import score_and_dedup

    rows = _fixture_rows()
    threshold = 0.85
    sdf = spark.createDataFrame(rows, ["__row_id__", "last_name", "zip"])
    out = score_and_dedup(
        sdf, block_col="zip", value_col="last_name", id_col="__row_id__",
        scorer_name="jaro_winkler", threshold=threshold,
    )
    sail_pairs = {(min(r["a"], r["b"]), max(r["a"], r["b"])) for r in out.collect()}
    assert sail_pairs == _reference_pairs(rows, threshold)
