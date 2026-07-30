"""Cost-aware blocking primary-key selection (#2021).

The exact-blocking "best case" branch picked a small-fixed-domain ``year``/``date``
column (e.g. ``birth_year``, ~65 distinct) as the SOLE primary blocking key on
identifier-poor person data -- a key whose block grows proportional to N, so it
explodes candidate pairs at scale (~7.7B at 1M). ``GOLDENMATCH_BLOCKING_COST_AWARE=1``
demotes such a key from the primary slot when a bounded fallback (a name key or a
bounded compound) exists; it stays available as a recall pass (#438).

Default OFF is byte-identical; these tests lock both states.
"""
import goldenmatch
import polars as pl
from goldenmatch.core.autoconfig import _cost_aware_blocking_enabled


def _year_pathology_df(n_clusters: int = 400, per: int = 5) -> pl.DataFrame:
    """Person-shape dedupe frame with the birth_year pathology: per-cluster names
    (bounded, ideal blocking keys) + a low-cardinality year column shared within a
    cluster + a near-unique email. No clean single identifier."""
    rows = []
    for c in range(n_clusters):
        year = 1950 + (c % 60)  # ~60 distinct years -> low-card blocking key
        for m in range(per):
            rows.append({
                "id": f"{c}-{m}",                       # unique surrogate (card 1.0)
                "first_name": f"First{c}",              # per-cluster, bounded
                "last_name": f"Last{c}",                # per-cluster, bounded
                "birth_year": str(year),                # low-card year
                "email": f"e{c}_{m % 3}@ex.com",        # mid-card: ~3 distinct/cluster
                "city": f"City{c % 8}",                 # geo
            })
    return pl.DataFrame(rows)


def _blocking_key_fields(cfg) -> list[list[str]]:
    bl = cfg.blocking
    return [list(k.fields) for k in (bl.keys or [])]


def test_flag_parsing(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_BLOCKING_COST_AWARE", raising=False)
    assert _cost_aware_blocking_enabled() is False
    for on in ("1", "true", "YES", "on"):
        monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", on)
        assert _cost_aware_blocking_enabled() is True
    for off in ("0", "false", "no", ""):
        monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", off)
        assert _cost_aware_blocking_enabled() is False


def test_cost_aware_demotes_year_primary(monkeypatch):
    df = _year_pathology_df()

    # Flag OFF: the pathology -- a single low-card year column is the sole primary.
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "0")
    cfg_off = goldenmatch.auto_configure_df(df)
    keys_off = _blocking_key_fields(cfg_off)
    assert keys_off == [["birth_year"]], (
        f"expected the year-only pathology under flag OFF, got {keys_off}"
    )

    # Flag ON: birth_year is demoted from the primary slot; the committed primary
    # is a bounded key (name / geo / email / compound), NOT the sole year column.
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "1")
    cfg_on = goldenmatch.auto_configure_df(df)
    keys_on = _blocking_key_fields(cfg_on)
    assert keys_on and keys_on != [["birth_year"]], (
        f"cost-aware should demote the sole year primary, got {keys_on}"
    )
    # The primary must not be a lone low-cardinality year/date field.
    assert keys_on[0] != ["birth_year"], keys_on


def test_off_is_default(monkeypatch):
    # No env set -> OFF (byte-identical legacy behaviour).
    monkeypatch.delenv("GOLDENMATCH_BLOCKING_COST_AWARE", raising=False)
    df = _year_pathology_df()
    cfg = goldenmatch.auto_configure_df(df)
    assert _blocking_key_fields(cfg) == [["birth_year"]]
