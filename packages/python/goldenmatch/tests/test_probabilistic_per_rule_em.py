import os
import numpy as np
import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _fs_per_rule_em_enabled, _estimate_m_one_pass, _build_comparison_matrix,
)

def test_per_rule_default_on_and_killswitch():
    assert _fs_per_rule_em_enabled() is True
    for v in ("0", "false", "DISABLED", "no", "  0 "):
        os.environ["GOLDENMATCH_FS_PER_RULE_EM"] = v
        try:
            assert _fs_per_rule_em_enabled() is False
        finally:
            os.environ.pop("GOLDENMATCH_FS_PER_RULE_EM", None)

def test_estimate_m_one_pass_skips_excluded_and_estimates_rest():
    # 6 rows: two clear dup clusters agreeing on name+city, distinct otherwise
    df = pl.DataFrame({
        "__row_id__": [0,1,2,3,4,5],
        "name": ["ann","ann","bob","bob","cara","dee"],
        "city": ["x","x","y","y","z","w"],
    })
    mk = MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="exact", levels=2, partial_threshold=0.9),
        MatchkeyField(field="city", scorer="exact", levels=2, partial_threshold=0.9),
    ])
    cols = ["name","city"]
    row_lookup = {r["__row_id__"]: r for r in df.select(["__row_id__"]+cols).to_dicts()}
    pairs = [(0,1),(2,3),(0,2),(1,4)]
    comp = _build_comparison_matrix(pairs, row_lookup, mk)
    u_probs = {"name":[0.5,0.5], "city":[0.5,0.5]}
    # exclude "name" (as if blocked on name): only "city" m should move off the prior
    m, p_match, converged, iterations = _estimate_m_one_pass(
        comp, mk, u_probs, excluded={"name"}, max_iterations=20, convergence=1e-3)
    assert "city" in m and "name" in m
    assert 0.0 < p_match <= 1.0 and isinstance(converged, bool) and iterations >= 1
    # city was estimated (not the bare exponential prior [1/3, 2/3] for 2 levels)
    assert m["city"] != [1/3, 2/3]
    # name was EXCLUDED -> left at the exponential prior
    assert m["name"] == [1/3, 2/3]
