"""S3 gate: Sail build_golden produces per-cluster golden field values
identical to the one-box merge_field over the same members (multi-member
clusters only). Both call merge_field -> parity by construction; the gate
proves the Sail join/group/collect_list plumbing assembles the right per-
cluster inputs. Small-N by design (production never collects golden to the
driver). Skips where the sail extra is absent; runs in the `sail` lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


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


_VALUE_COLS = ["first_name", "email"]


def _source_rows():
    """(row_id, first_name, email). Each multi-member cluster has a clear
    most_complete winner per field (one non-null, or a unique-longest value),
    so the survivor is order-independent under collect_list's arbitrary order."""
    return [
        # cluster A (members 0,1): first_name winner "Jonathan" (vs null),
        #   email winner "jon@x.com" (vs null).
        (0, "Jonathan", None),
        (1, None, "jon@x.com"),
        # cluster B (members 2,3,4): first_name "Margaret" (unique-longest vs
        #   "Marg", None), email "marg@y.com" (vs None, None).
        (2, "Marg", None),
        (3, "Margaret", "marg@y.com"),
        (4, None, None),
        # singleton (member 5): excluded from golden.
        (5, "Solo", "solo@z.com"),
    ]


def _assignments():
    # (cluster_id, member_id). cluster_id = min member id (S2 convention).
    return [(0, 0), (0, 1), (2, 2), (2, 3), (2, 4), (5, 5)]


def _reference_golden(rows, assignments, value_cols, strategy):
    from collections import defaultdict

    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    by_id = {r[0]: dict(zip(["__row_id__", *value_cols], r)) for r in rows}
    members = defaultdict(list)
    for cid, mid in assignments:
        members[cid].append(mid)
    rule = GoldenFieldRule(strategy=strategy)
    out = {}
    for cid, mids in members.items():
        if len(mids) < 2:
            continue
        rec = {}
        for c in value_cols:
            merged, _c, _s = merge_field([by_id[m][c] for m in mids], rule)
            rec[c] = None if merged is None else str(merged)
        out[cid] = rec
    return out


def _sail_golden(out_df, value_cols):
    return {
        int(r["cluster_id"]): {c: r[c] for c in value_cols}
        for r in out_df.collect()
    }


def test_sail_golden_content_parity(spark):
    from goldenmatch.sail.golden import build_golden

    rows = _source_rows()
    assignments = _assignments()
    source_df = spark.createDataFrame(rows, ["__row_id__", *_VALUE_COLS])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])

    out = build_golden(assign_df, source_df, value_cols=_VALUE_COLS)
    got = _sail_golden(out, _VALUE_COLS)
    expected = _reference_golden(rows, assignments, _VALUE_COLS, "most_complete")
    assert got == expected
    # Singleton cluster 5 excluded (redundant with the dict equality; explicit
    # intent marker).
    assert 5 not in got
