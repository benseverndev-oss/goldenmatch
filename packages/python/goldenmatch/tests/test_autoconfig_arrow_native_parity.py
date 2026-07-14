"""Arrow-native zero-config parity gate (spine-port Route B).

`GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1` skips the `auto_configure_df` boundary
coercion and keeps a bare ``pa.Table`` native through the controller, which routes
sample dedupes to the arrow-native ``bucket`` scorer (`_maybe_bucket_route_arrow`)
and runs the config-building detectors (exclusions / discriminative demotion /
source-partition / indicators) through the Frame seam.

These tests assert the arrow-native path is EQUIVALENT to the polars path:
- **cluster-equivalence** (the outcome that matters): zero-config ``dedupe_df``
  finds the same number of duplicate rows on a ``pa.Table`` (flag on) as on the
  equivalent ``pl.DataFrame``. Robust across dataset shapes.
- **config-equivalence** on shapes without a near-tie: same matchkeys.

Note: blocking-COLUMN selection can differ on a genuine near-tie between two
equally-valid low-cardinality keys because ``Frame.sample`` is statistical-not-byte
across backends BY DESIGN (see the polars-eviction design) -- so we assert
cluster-equivalence (unaffected) rather than byte-identical blocking there.

The full zero-config Polars-FREE tripwire (`test_zero_config_no_polars.py`) stays
xfail on a separate blocker: the clustering result-wrapping
`cluster.build_clusters_arrow_native` (cluster.py:2027) still builds polars
``ClusterFrames`` from the arrow kernel output -- the 3.x engine-descent eviction,
not the autoconfig port. This gate runs with polars PRESENT.
"""

import os

import polars as pl
import pyarrow as pa
import pytest


@pytest.fixture(autouse=True)
def _no_autoconfig_memory():
    prev = os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY")
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    yield
    if prev is None:
        os.environ.pop("GOLDENMATCH_AUTOCONFIG_MEMORY", None)
    else:
        os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = prev


def _arrow_native(on: bool):
    if on:
        os.environ["GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE"] = "1"
    else:
        os.environ.pop("GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE", None)


def _dupe_count(res) -> int:
    d = res.dupes
    if d is None:
        return 0
    return d.num_rows if hasattr(d, "num_rows") else d.height


# Dataset shapes: (name, data-dict). Deterministic (no RNG so arrow==polars
# construction is identical).
_SHAPES = {
    "exact-id-plus-names": {
        "cust_id": [f"C{i:05d}" for i in range(400)],
        "first": (["ann", "bob", "cara", "dan", "eve", "fay"] * 67)[:400],
        "last": (["smith", "jones", "lee", "poe", "ray", "kim"] * 67)[:400],
        "zip": [str(10000 + (i % 50)) for i in range(400)],
    },
    "all-fuzzy": {
        "first": (["ann", "bob", "cara"] * 100),
        "last": (["smith", "jones", "lee"] * 100),
    },
    "shared-email-switchboard": {
        # email shared across different-name records -> discriminative veto path
        "email": [f"user{i % 250}@x.com" for i in range(300)],
        "first": (["ann", "bob"] * 150),
        "last": (["smith", "jones"] * 150),
    },
    "multi-source": {
        # "src" is a user source-indicator column (matches _SOURCE_NAME_RE for
        # #858), NOT the internal "__source__" bookkeeping name.
        "src": (["A", "B"] * 150),
        "acct": [f"A{i % 280:04d}" for i in range(300)],
        "first": (["ann", "bob", "cy"] * 100),
        "last": (["smith", "jones", "lee"] * 100),
    },
}


def _config_matchkeys(cfg):
    return sorted(
        (mk.type, tuple(sorted(f.field for f in (mk.fields or []) if f.field)))
        for mk in cfg.get_matchkeys()
    )


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_arrow_native_cluster_equivalence(shape):
    """Zero-config ``dedupe_df`` finds the same duplicate count on a pa.Table
    (arrow-native flag on) as on the equivalent pl.DataFrame."""
    from goldenmatch import dedupe_df

    data = _SHAPES[shape]
    _arrow_native(False)
    res_pl = dedupe_df(pl.DataFrame(data), config=None)
    _arrow_native(True)
    try:
        res_pa = dedupe_df(pa.table(data), config=None)
    finally:
        _arrow_native(False)
    assert _dupe_count(res_pl) == _dupe_count(res_pa), (
        f"{shape}: polars dupes={_dupe_count(res_pl)} != arrow dupes={_dupe_count(res_pa)}"
    )


@pytest.mark.parametrize("shape", ["exact-id-plus-names", "shared-email-switchboard"])
def test_arrow_native_config_matchkey_equivalence(shape):
    """On shapes without a near-tie blocking choice, the arrow-native config's
    matchkeys match the polars config exactly (the discriminative veto /
    exclusion / source-partition detectors decide identically)."""
    from goldenmatch.core.autoconfig import auto_configure_df

    data = _SHAPES[shape]
    _arrow_native(False)
    cfg_pl = auto_configure_df(pl.DataFrame(data), _skip_finalize=True)
    _arrow_native(True)
    try:
        cfg_pa = auto_configure_df(pa.table(data), _skip_finalize=True)
    finally:
        _arrow_native(False)
    assert _config_matchkeys(cfg_pl) == _config_matchkeys(cfg_pa)
