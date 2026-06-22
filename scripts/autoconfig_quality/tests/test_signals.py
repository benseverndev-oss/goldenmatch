from scripts.autoconfig_quality.anchors import make_healthcare_df
from scripts.autoconfig_quality.signals import extract_signals


def test_extract_signals_sparse_zip():
    # n=30k is the scale where the zip5 regression lives: 5k-distinct zips at
    # 50% present saturate to ~0.32 cardinality, so the corrected classifier
    # keeps zip5 as `zip` (the old 0.95 floor was fooled at small N into
    # `identifier`), and the blocking-decouple keeps zip5 bounding the compound.
    df = make_healthcare_df(30_000, seed=715, zip_present=0.5).drop("matching_id")
    sig = extract_signals(df)
    assert sig["classification"]["zip5"] == "zip"           # not fooled into identifier
    assert "zip5" in sig["blocking_fields"]                  # the decouple fix
    assert sig["blocking_cost"]["candidate_pairs"] < 50_000  # bounded, not 8.9M
    assert "max_block" in sig["blocking_cost"]
    assert isinstance(sig["exact_matchkeys"], list)
    assert "backend" in sig["planner_rung"] and "rule_name" in sig["planner_rung"]
