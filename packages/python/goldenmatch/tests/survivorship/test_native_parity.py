import polars as pl
from goldenmatch.config.schemas import GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import build_golden_records_batch
from goldenmatch.core.survivorship.native import survivorship_native_eligible


def _slow_oracle(multi_df, rules):
    """Slow path on a __row_id__-deterministic frame; returns the golden
    DataFrame (values + __golden_confidence__; provenance=False)."""
    df = multi_df.sort(["__cluster_id__", "__row_id__"])
    rows = build_golden_records_batch(df, rules, provenance=False)
    golden = []
    for rec in rows:
        row = {"__cluster_id__": rec["__cluster_id__"],
               "__golden_confidence__": rec.get("__golden_confidence__")}
        for col, info in rec.items():
            if col in ("__cluster_id__", "__golden_confidence__", "__survivorship_prov__"):
                continue
            row[col] = info["value"] if isinstance(info, dict) and "value" in info else info
        golden.append(row)
    return pl.DataFrame(golden).sort("__cluster_id__")


def assert_parity(multi_df, rules, compare_confidence=True):
    """Byte-identical golden output: native path == slow oracle (provenance=False)."""
    from goldenmatch.core.survivorship.native import build_survivorship_native
    native = build_survivorship_native(multi_df, rules).sort("__cluster_id__")
    oracle = _slow_oracle(multi_df, rules)
    cols = sorted(c for c in oracle.columns if compare_confidence or c != "__golden_confidence__")
    assert native.select(cols).equals(oracle.select(cols)), (
        f"PARITY MISMATCH\nnative:\n{native.select(cols)}\noracle:\n{oracle.select(cols)}"
    )


def test_eligible_false_until_implemented():
    rules = GoldenRulesConfig(default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])])
    assert survivorship_native_eligible(rules, provenance=False) is False


def test_slow_path_deterministic_on_ties():
    # tie-heavy: 2-row clusters where both rows have the same populated count
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "street": ["A St", "B St", "C St", "D St"],   # both rows in each cluster 2/2 -> tie
        "city": ["LA", "NY", "SF", "DC"],
    })
    rules = GoldenRulesConfig(default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city"])])
    a = _slow_oracle(df, rules)
    b = _slow_oracle(df.sample(fraction=1.0, shuffle=True, seed=1), rules)
    assert a.equals(b)   # winner = lowest __row_id__ regardless of input order
