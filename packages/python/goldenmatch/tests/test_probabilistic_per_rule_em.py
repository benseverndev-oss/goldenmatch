import os

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _build_comparison_matrix,
    _estimate_m_one_pass,
    _fs_per_rule_em_enabled,
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


from goldenmatch.core.probabilistic import train_em


def _df_person():
    # name="smith" and name="jones" each have 6 records forming 3 true pairs that AGREE on
    # postcode within a pair but DIFFER across pairs -> within each name block: 15 pairs
    # (3 true matches agreeing on postcode, 12 non-matches disagreeing). Plus 12 distinct
    # singletons so u[name]/u[postcode] reflect realistic low random-collision rates. This
    # clears the per-pass <10-pair guard on the name-blocked pass (30 pairs) so the per-rule
    # mechanism actually trains (the original 8-row fixture fell back to _fallback_result).
    names = (["smith"] * 6 + ["jones"] * 6
             + ["lee", "ng", "ono", "poe", "kim", "fox", "roe", "doe", "amos", "bell", "cole", "dean"])
    postcodes = (["AA1", "AA1", "BB2", "BB2", "CC3", "CC3",
                  "DD4", "DD4", "EE5", "EE5", "FF6", "FF6"]
                 + ["G01", "H02", "I03", "J04", "K05", "L06", "M07", "N08", "O09", "P10", "Q11", "R12"])
    return pl.DataFrame({
        "__row_id__": list(range(len(names))),
        "name": names,
        "postcode": postcodes,
    })

def _mk():
    return MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="exact", levels=2, partial_threshold=0.9),
        MatchkeyField(field="postcode", scorer="exact", levels=2, partial_threshold=0.9),
    ])

def _passes_blocks(df, fieldsets):
    # build one BlockResult-like per pass via the real blocker
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    out = []
    for fs in fieldsets:
        cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=fs)])
        out.append((fs, build_blocks(df.lazy(), cfg)))
    return out

def test_per_rule_postcode_disagree_penalty_is_strong():
    df, mk = _df_person(), _mk()
    # passes: block on name (postcode free) AND block on postcode (name free)
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em = train_em(df, mk, passes=passes)
    # postcode estimated in the name-blocked run where it varies -> disagree weight clearly negative
    assert em.match_weights["postcode"][0] <= -1.0
    # name estimated in the postcode-blocked run
    assert "name" in (em.tf_tables or {}) or em.match_weights["name"][1] > 0

def test_per_rule_beats_single_run_on_disagree_penalty():
    df, mk = _df_person(), _mk()
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em_perrule = train_em(df, mk, passes=passes)
    # single-run kill-switch path: block on name only, postcode m corrupted
    os.environ["GOLDENMATCH_FS_PER_RULE_EM"] = "0"
    try:
        blocks = passes[0][1]
        em_single = train_em(df, mk, blocks=blocks, blocking_fields=["name"])
    finally:
        os.environ.pop("GOLDENMATCH_FS_PER_RULE_EM", None)
    # per-rule gives a stronger (more negative) postcode-disagree weight than single-run
    assert em_perrule.match_weights["postcode"][0] < em_single.match_weights["postcode"][0]

def test_per_rule_u_not_overridden_for_sometimes_blocked_field():
    df, mk = _df_person(), _mk()
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em = train_em(df, mk, passes=passes)
    # name is a block key in pass 1 but u must be the random-pair estimate, NOT neutral 0.5
    assert em.u_probs["name"] != [0.5, 0.5]

def test_per_rule_fallback_for_always_blocked_field():
    df, mk = _df_person(), _mk()
    # both passes block on name -> name free in NO pass -> fixed prior fallback
    passes = _passes_blocks(df, [["name"], ["name"]])
    em = train_em(df, mk, passes=passes)
    assert em.match_weights["name"] == [-3.0, 3.0]  # fixed 2-level prior
