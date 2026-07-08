from __future__ import annotations

from goldenflow._polars_lazy import pl
from goldenflow.transforms import register_transform
from goldenflow.transforms._native import (
    boolean_normalize_native,
    category_normalize_key_native,
    gender_standardize_native,
    null_standardize_native,
)

_TRUE_VALUES = {"yes", "y", "1", "true", "t"}
_FALSE_VALUES = {"no", "n", "0", "false", "f"}
_NULL_VALUES = {"n/a", "null", "none", "na", "nil", "nan", "-", ""}

# Pure-Python reference for goldenflow-core's ``categorical`` kernel. MUST
# reproduce the Rust kernel byte-for-byte (asserted by
# tests/transforms/test_identifiers_parity.py over
# tests/parity/identifiers_corpus.jsonl).
#
# NOTE on the mapping-based transforms (``category_standardize`` /
# ``category_from_file``): the caller-supplied variant->canonical mapping is
# RUNTIME DATA (a function param, or loaded from a CSV/YAML file), not logic,
# so goldenflow-core does NOT own it -- there is no dict lookup kernel. What
# IS owned is ``category_normalize_key`` (the shared trim+lowercase key
# derivation used before any lookup, fixed or caller-supplied); the two
# mapping transforms below call it to derive the lookup key, then keep the
# dict-lookup-with-fallback loop in pure Python.


def _category_normalize_key_py(val: str) -> str:
    return val.strip().lower()


def _boolean_normalize_py(val: str | None) -> bool | None:
    if val is None:
        return None
    v = _category_normalize_key_py(val)
    if v in _TRUE_VALUES:
        return True
    if v in _FALSE_VALUES:
        return False
    return None


def _gender_standardize_py(val: str | None) -> str | None:
    if val is None:
        return None
    _map = {"male": "M", "m": "M", "female": "F", "f": "F"}
    return _map.get(_category_normalize_key_py(val), val)


def _null_standardize_py(val: str | None) -> str | None:
    if val is None:
        return None
    if _category_normalize_key_py(val) in _NULL_VALUES:
        return None
    return val


def _category_normalize_key_series(series: pl.Series) -> pl.Series:
    """Vectorized key-normalization shared by ``category_standardize`` and
    ``category_from_file``. Native-first (goldenflow-core's
    ``categorical::category_normalize_key`` kernel); the pure-Python fallback
    below is the byte-exact reference this kernel replicates."""
    native = category_normalize_key_native()
    if native is not None:
        return native(series)
    return series.map_elements(
        lambda v: None if v is None else _category_normalize_key_py(v),
        return_dtype=pl.Utf8,
    )


@register_transform(
    name="boolean_normalize", input_types=["boolean", "string"], auto_apply=False, priority=50,
    mode="series", scalar=_boolean_normalize_py, scalar_dtype="bool",
)
def boolean_normalize(series: pl.Series) -> pl.Series:
    """Parse loose boolean-ish strings (yes/no/y/n/1/0/true/false/t/f).

    Native-first (goldenflow-core's ``categorical::boolean_normalize``
    kernel); the pure-Python fallback below is the byte-exact reference this
    kernel replicates.
    """
    native = boolean_normalize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_boolean_normalize_py, return_dtype=pl.Boolean)


@register_transform(
    name="gender_standardize", input_types=["string"], auto_apply=False, priority=50, mode="series",
    scalar=_gender_standardize_py,
)
def gender_standardize(series: pl.Series) -> pl.Series:
    """Standardize gender strings to ``M``/``F``; anything else passes
    through unchanged.

    Native-first (goldenflow-core's ``categorical::gender_standardize``
    kernel); the pure-Python fallback below is the byte-exact reference this
    kernel replicates.
    """
    native = gender_standardize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_gender_standardize_py, return_dtype=pl.Utf8)


@register_transform(
    name="null_standardize", input_types=["string"], auto_apply=True, priority=80, mode="series",
    scalar=_null_standardize_py,
)
def null_standardize(series: pl.Series) -> pl.Series:
    """Map null-sentinel strings (n/a, null, none, na, nil, nan, -, empty) to
    a real null; anything else passes through unchanged.

    Native-first (goldenflow-core's ``categorical::null_standardize``
    kernel); the pure-Python fallback below is the byte-exact reference this
    kernel replicates.
    """
    native = null_standardize_native()
    if native is not None:
        return native(series)
    return series.map_elements(_null_standardize_py, return_dtype=pl.Utf8)


def _category_passthrough(val: str | None) -> str | None:
    """Columnar scalar for ``category_standardize``: identity. The variant->canonical
    mapping is a dict passed only via the direct API (``category_standardize(series,
    mapping=...)``) — there is NO config/ops-string channel for a dict, so through the
    config-driven columnar path this transform is a no-op, exactly as the Polars engine
    is (``mapping=None`` -> ``return series``). Wiring identity makes the config case
    columnar-ready and byte-identical; the mapping-carrying direct call still runs the
    Polars body."""
    return val


def _category_from_file_factory(params: list[str]):
    """Columnar scalar factory for ``category_from_file:<path>``. Loads the
    variant->canonical mapping Polars-free (stdlib ``csv`` / ``yaml``) ONCE, then returns
    a per-element closure byte-identical to the Polars engine: ``mapping.get(trim+lower
    key, original)``. No path (or an unknown suffix) -> identity, matching the engine's
    ``if not lookup_path: return series``."""
    import csv
    from pathlib import Path

    lookup_path = params[0] if params else None
    if not lookup_path:
        return _category_passthrough
    p = Path(lookup_path)
    mapping: dict[str, str] = {}
    if p.suffix == ".csv":
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                mapping[row["variant"].lower()] = row["canonical"]
    elif p.suffix in (".yaml", ".yml"):
        import yaml
        with open(p) as f:
            raw = yaml.safe_load(f) or {}
        for canonical, variants in raw.items():
            for v in variants:
                mapping[v.lower()] = canonical
    else:
        return _category_passthrough

    def _apply(val: str | None) -> str | None:
        if val is None:
            return None
        return mapping.get(_category_normalize_key_py(val), val)

    return _apply


@register_transform(
    name="category_standardize",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
    scalar=_category_passthrough,
)
def category_standardize(
    series: pl.Series, mapping: dict[str, list[str]] | None = None
) -> pl.Series:
    """Map variant values to canonical values. mapping: {canonical: [variant1, variant2, ...]}

    The mapping is runtime DATA supplied by the caller, so the dict lookup
    stays in Python; only the key-normalization step (trim+lowercase) is
    native-first via goldenflow-core's ``categorical::category_normalize_key``
    kernel.
    """
    if not mapping:
        return series
    lookup: dict[str, str] = {}
    for canonical, variants in mapping.items():
        for v in variants:
            lookup[v.lower()] = canonical

    keys = _category_normalize_key_series(series).to_list()
    originals = series.to_list()
    result = [
        (lookup.get(key, original) if original is not None else None)
        for key, original in zip(keys, originals)
    ]
    return pl.Series(series.name, result, dtype=pl.Utf8)


@register_transform(
    name="category_from_file",
    input_types=["string"],
    auto_apply=False,
    priority=45,
    mode="series",
    scalar_factory=_category_from_file_factory,
)
def category_from_file(
    series: pl.Series, lookup_path: str | None = None
) -> pl.Series:
    """Load mapping from a CSV/YAML file and standardize values.
    CSV must have columns: variant, canonical.

    Same native/Python split as ``category_standardize``: the file-loaded
    mapping is runtime data (Python-only); only key-normalization is
    native-first.
    """
    if not lookup_path:
        return series
    from pathlib import Path
    p = Path(lookup_path)
    if p.suffix == ".csv":
        import polars as pl_inner
        lookup_df = pl_inner.read_csv(p)
        mapping: dict[str, str] = {}
        for row in lookup_df.iter_rows(named=True):
            mapping[row["variant"].lower()] = row["canonical"]
    elif p.suffix in (".yaml", ".yml"):
        import yaml
        with open(p) as f:
            raw = yaml.safe_load(f) or {}
        mapping = {}
        for canonical, variants in raw.items():
            for v in variants:
                mapping[v.lower()] = canonical
    else:
        return series

    keys = _category_normalize_key_series(series).to_list()
    originals = series.to_list()
    result = [
        (mapping.get(key, original) if original is not None else None)
        for key, original in zip(keys, originals)
    ]
    return pl.Series(series.name, result, dtype=pl.Utf8)
