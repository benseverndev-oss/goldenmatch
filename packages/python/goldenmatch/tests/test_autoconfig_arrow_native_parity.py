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
    # Set BOTH values explicitly. Since the flag now defaults ON (2026-07-14),
    # the polars baseline (`on=False`) must set "0" -- popping would inherit the
    # new default and stop being a real polars-vs-arrow comparison.
    os.environ["GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE"] = "1" if on else "0"


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
    "dob-person": {
        # a date/dob column -- the FS-v2 date-admission + date-year blocking
        # detectors must decide identically arrow-vs-polars.
        "first": (["ann", "bob", "cara", "dan"] * 75),
        "last": (["smith", "jones", "lee", "poe"] * 75),
        "dob": [f"19{60 + (i % 40):02d}-0{1 + (i % 9)}-1{i % 10}" for i in range(300)],
    },
    "high-null-aux": {
        # a mostly-null auxiliary column -- the blocking null-rate guard (>20%
        # skip) must fire identically on both backends.
        "first": (["ann", "bob", "cara"] * 100),
        "last": (["smith", "jones", "lee"] * 100),
        "aux": [(f"x{i}" if i % 10 == 0 else None) for i in range(300)],
    },
    "typo-names": {
        # systematic near-dups (typo'd first name, same last) -> the fuzzy
        # name-scorer path drives the dup count; must match cross-backend.
        "first": (["michael", "micheal", "sara", "sarah"] * 75),
        "last": (["brown", "brown", "davis", "davis"] * 75),
        "zip": [str(20000 + (i % 40)) for i in range(300)],
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


def test_arrow_native_is_the_default():
    """With GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE UNSET, a pa.Table zero-config run
    engages the arrow-native path by default (2026-07-14 flip) -- i.e. its config
    equals an explicitly-flag-on run and its dupes equal the polars baseline."""
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_df

    data = _SHAPES["exact-id-plus-names"]
    os.environ.pop("GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE", None)  # rely on the default
    cfg_default = auto_configure_df(pa.table(data), _skip_finalize=True)
    res_default = dedupe_df(pa.table(data), config=None)

    _arrow_native(True)  # explicit on
    try:
        cfg_explicit = auto_configure_df(pa.table(data), _skip_finalize=True)
    finally:
        _arrow_native(False)
    res_pl = dedupe_df(pl.DataFrame(data), config=None)

    assert _config_matchkeys(cfg_default) == _config_matchkeys(cfg_explicit)
    assert _dupe_count(res_default) == _dupe_count(res_pl)


def _compound_shape():
    """400 rows where every SINGLE column is oversized (4 blocks of 100) but the
    compound (first, last) pair is bounded (16 blocks of 25) -- the shape that
    routes ``build_blocking`` into ``_build_compound_blocking``."""
    return {
        "first": [f"n{i % 4}" for i in range(400)],
        "last": [f"s{(i // 4) % 4}" for i in range(400)],
    }


def test_build_compound_blocking_accepts_arrow_table():
    """#1852: ``_build_compound_blocking`` AttributeError'd on a ``pa.Table``
    (``df[col].null_count()`` / ``df.height`` / ``df.group_by`` are polars-only)
    -- live on 3.3.1/3.4.0 with GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE default-on.
    It must now run on Arrow via the Frame seam and return the SAME config the
    polars input yields (byte-value-equivalent on this deterministic shape)."""
    from goldenmatch.core.autoconfig import ColumnProfile, _build_compound_blocking

    data = _compound_shape()
    profiles = [
        ColumnProfile(name="first", dtype="str", col_type="string", confidence=1.0,
                      cardinality_ratio=4 / 400),
        ColumnProfile(name="last", dtype="str", col_type="string", confidence=1.0,
                      cardinality_ratio=4 / 400),
    ]

    cfg_pa = _build_compound_blocking(profiles, pa.table(data), max_safe_block=50, max_null_rate=0.2)
    cfg_pl = _build_compound_blocking(
        profiles, pl.DataFrame(data), max_safe_block=50, max_null_rate=0.2
    )

    # Arrow no longer raises AttributeError and finds a bounded compound.
    assert cfg_pa is not None
    assert cfg_pa.strategy == "multi_pass"
    # Cross-backend equivalence: same passes selected on the deterministic shape.
    assert cfg_pl is not None
    assert [(list(p.fields), list(p.transforms)) for p in cfg_pa.passes] == [
        (list(p.fields), list(p.transforms)) for p in cfg_pl.passes
    ]


def _wide_sparse_union_shape():
    """Deterministic wide/sparse CRM diagonal union: 4 strong-id columns, each
    present only in ITS OWN source (75% null overall), plus a shared name +
    geo. No single exact key clears the 0.20 null ceiling, so ``build_blocking``
    routes into the #1207 per-identifier blocking UNION -- whose scale-safety
    gate (``_id_pass_scale_safe_nonnull``) used raw ``df.filter(pl.col(...))``
    under a bare ``except`` that turned the AttributeError on a ``pa.Table``
    into ``return False`` for EVERY id pass. The union then silently collapsed
    to the name-only fallback on the arrow lane (the second, subtler #1852
    failure mode -- degraded blocking, not a crash). No RNG so the pa/pl inputs
    are byte-identical by construction."""
    n = 120  # rows per source
    first = ["John", "Jane", "Bob", "Alice", "Sam", "Mary"]
    last = ["Smith", "Jones", "Lee", "Brown", "Davis", "Miller"]
    city = ["Boston", "Austin", "Denver", "Reno"]
    ids = ["account_id", "owner_id", "order_id", "who_id"]
    cols: dict[str, list] = {c: [] for c in ["first", "last", "city", *ids]}
    for idc in ids:
        for i in range(n):
            cols["first"].append(first[i % len(first)])
            cols["last"].append(last[(i // 2) % len(last)])
            cols["city"].append(city[i % len(city)])
            for k in ids:
                cols[k].append(f"{idc[:2].upper()}{i:05d}" if k == idc else None)
    return cols


def _blocking_passes(blk) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    if blk is not None:
        if blk.keys:
            out += [tuple(k.fields) for k in blk.keys]
        if getattr(blk, "passes", None):
            out += [tuple(p.fields) for p in blk.passes]
    return out


def test_build_blocking_id_union_arrow_parity():
    """#1852 (mode 2): ``build_blocking`` on a wide/sparse frame emits the SAME
    per-identifier blocking union on a ``pa.Table`` as on a ``pl.DataFrame``.

    Feeds IDENTICAL profiles + n_rows_full to ``build_blocking`` for both
    backends (as ``test_build_compound_blocking_accepts_arrow_table`` does),
    so the only variable is the frame backend -- no ``Frame.sample`` in play,
    hence a byte-deterministic cross-backend assertion. Before #1852 the arrow
    branch dropped the id union and fell to name-only blocking; the passes then
    differed (a silent recall/precision divergence, not an exception)."""
    from goldenmatch.core.autoconfig import build_blocking, profile_columns

    data = _wide_sparse_union_shape()
    pl_df = pl.DataFrame(data)
    # One profile set, shared across both backends (isolates build_blocking).
    profiles = profile_columns(pl_df)

    blk_pl = build_blocking(profiles, pl_df, n_rows_full=pl_df.height)
    blk_pa = build_blocking(profiles, pa.table(data), n_rows_full=pl_df.height)

    passes_pl = _blocking_passes(blk_pl)
    passes_pa = _blocking_passes(blk_pa)

    # The id union actually formed (not the name-only fallback): the strong-id
    # columns appear as passes on the polars baseline...
    id_cols = {"account_id", "owner_id", "order_id", "who_id"}
    assert id_cols & {f for fields in passes_pl for f in fields}, (
        f"baseline should block on the strong ids, got {passes_pl}"
    )
    # ...and the arrow lane selects the identical passes.
    assert passes_pa == passes_pl, (
        f"arrow blocking diverged from polars: arrow={passes_pa} polars={passes_pl}"
    )
    assert blk_pa.strategy == blk_pl.strategy


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
