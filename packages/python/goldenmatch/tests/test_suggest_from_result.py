import polars as pl
from goldenmatch import dedupe_df
from goldenmatch.core.suggest.adapter import suggest_from_result
from goldenmatch.core.suggest.types import SuggestionsNativeRequired


def _person_df():
    # realistic person shape so auto-config builds a non-degenerate config
    import random
    random.seed(0)
    first = ["John","Jane","Bob","Alice","Tom","Sara","Mike","Lisa"]
    last = ["Smith","Jones","Brown","Davis","Miller","Wilson","Moore","Clark"]
    rows = []
    for i in range(60):
        f = random.choice(first); l = random.choice(last)
        rows.append({"name": f"{f} {l}", "email": f"{f}.{l}@x.com".lower(), "zip": f"{random.randint(10000,10005)}"})
        if i % 3 == 0:  # inject a near-dup
            rows.append({"name": f"{f} {l}", "email": f"{f}.{l}@x.com".lower(), "zip": f"{random.randint(10000,10005)}"})
    return pl.DataFrame(rows)


def test_graceful_without_native(monkeypatch):
    # force the kernel absent -> [] (no raise)
    import goldenmatch.core.suggest.adapter as ad
    monkeypatch.setattr(ad, "_require_kernel",
                        lambda: (_ for _ in ()).throw(SuggestionsNativeRequired("no native")))
    df = _person_df()
    res = dedupe_df(df)
    assert suggest_from_result(res, df) == []


# The native parity test (suggest_from_result raw/verified == review_config) lives
# in test_healer_default_e2e_native.py, gated on the real `suggest_config` symbol
# so it actually runs in the native CI lane (this module's prior copy was
# skipif(True) -> permanently dead).
