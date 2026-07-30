"""Cost-aware blocking primary-key selection (#2021).

The exact-blocking "best case" branch picked a small-fixed-domain ``year``/``date``
column (e.g. ``birth_year``, ~65 distinct) as the SOLE primary blocking key on
identifier-poor person data -- a key whose block grows proportional to N, so it
explodes candidate pairs at scale (~7.7B at 1M). ``GOLDENMATCH_BLOCKING_COST_AWARE=1``
demotes such a key from the primary slot when a bounded fallback (a name key or a
bounded compound) exists; it stays available as a recall pass (#438).

Default OFF is byte-identical; these tests lock both states.
"""
import types

import goldenmatch
import polars as pl
from goldenmatch.core.autoconfig import (
    _cost_aware_blocking_enabled,
    _is_bibliographic_dataset,
)


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


def _biblio_year_pathology_df(n_clusters: int = 400, per: int = 5) -> pl.DataFrame:
    """The SAME cardinality pathology as ``_year_pathology_df`` but with
    BIBLIOGRAPHIC column names (title/authors/year/venue): the ``year`` is a
    publication year, a legitimately strong same-year blocking signal. Used to prove
    the cost-aware demotion is domain-routed OFF here (it must NOT demote year)."""
    rows = []
    for c in range(n_clusters):
        year = 1950 + (c % 60)
        for m in range(per):
            rows.append({
                "doi": f"{c}-{m}",                      # unique surrogate
                "title": f"Title{c}",                   # per-cluster
                "authors": f"Authors{c}",               # per-cluster
                "year": str(year),                      # low-card publication year
                "venue": f"Venue{c % 8}",               # low-card venue
            })
    return pl.DataFrame(rows)


def _blocking_key_fields(cfg) -> list[list[str]]:
    bl = cfg.blocking
    return [list(k.fields) for k in (bl.keys or [])]


def _bc_keys(bl) -> list[list[str]]:
    """Key fields off a raw BlockingConfig (build_blocking's return)."""
    return [list(k.fields) for k in (bl.keys or [])]


def test_is_bibliographic_dataset_predicate():
    def _p(*names):
        return [types.SimpleNamespace(name=n) for n in names]

    # Person shape (first_name/last_name/email/birth_year) -> NOT bibliographic
    # (person signals dominate; birth_year's "year"/"birth" tokens don't flip it).
    assert _is_bibliographic_dataset(
        _p("id", "first_name", "last_name", "birth_year", "email", "city")
    ) is False
    # Bibliographic shape -> True.
    assert _is_bibliographic_dataset(
        _p("doi", "title", "authors", "year", "venue")
    ) is True
    # No signal at all -> not bibliographic (fail-safe: demotion applies).
    assert _is_bibliographic_dataset(_p("a", "b", "c")) is False


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


def test_cost_aware_preserves_year_on_bibliographic(monkeypatch):
    # Domain routing: on BIBLIOGRAPHIC data the year is a publication year (a strong
    # same-year blocking signal), so the cost-aware demotion must be SKIPPED -- flag
    # ON must produce the SAME blocking as flag OFF (year preserved, not demoted).
    # Exercise build_blocking directly (the routing site) to avoid the full-pipeline
    # scoring of the synthetic biblio frame.
    from goldenmatch.core.autoconfig import build_blocking, profile_columns

    df = _biblio_year_pathology_df()
    profiles = profile_columns(df)

    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "0")
    keys_off = _bc_keys(build_blocking(profiles, df))

    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "1")
    keys_on = _bc_keys(build_blocking(profiles, df))

    assert keys_on == keys_off, (
        f"bibliographic data must be exempt from cost-aware demotion: "
        f"OFF={keys_off} ON={keys_on}"
    )


def test_cost_aware_demotes_year_on_person_at_build_blocking(monkeypatch):
    # Contrast to the bibliographic case above, through the SAME build_blocking seam:
    # on PERSON-named columns the year IS demoted under the flag.
    from goldenmatch.core.autoconfig import build_blocking, profile_columns

    df = _year_pathology_df()
    profiles = profile_columns(df)

    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "0")
    keys_off = _bc_keys(build_blocking(profiles, df))
    monkeypatch.setenv("GOLDENMATCH_BLOCKING_COST_AWARE", "1")
    keys_on = _bc_keys(build_blocking(profiles, df))

    # OFF picks the lone year; ON demotes it (routing did NOT exempt person data).
    assert keys_off == [["birth_year"]], keys_off
    assert keys_on != [["birth_year"]], keys_on


def test_off_is_default(monkeypatch):
    # No env set -> OFF (byte-identical legacy behaviour).
    monkeypatch.delenv("GOLDENMATCH_BLOCKING_COST_AWARE", raising=False)
    df = _year_pathology_df()
    cfg = goldenmatch.auto_configure_df(df)
    assert _blocking_key_fields(cfg) == [["birth_year"]]
