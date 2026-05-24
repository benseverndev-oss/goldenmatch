"""DuckDB UDFs for GoldenFlow transforms (v0.5 of dbt-goldensuite).

Closes part of goldenmatch issue #465 Tier 1.1 (DuckDB layer). The
Postgres pgrx wrappers + dbt macros land alongside this.

Each UDF wraps the equivalent goldenflow Series-level transform. We
construct a 1-element pl.Series, dispatch through goldenflow's
transform registry, and unbox the result. That's the cheapest path
to byte-equality with the Python sibling.

Registered alongside the other goldenmatch UDFs via
`register_goldenflow_functions(con)`. Call site:
`goldenmatch_duckdb.functions.register(con)` already imports + calls
this when goldenflow is available; consumers who don't have
goldenflow installed get a clear ImportError + skip.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _try_import_goldenflow() -> bool:
    try:
        import goldenflow  # noqa: F401
        return True
    except ImportError:
        return False


def _wrap_series_transform(transform_name: str) -> Callable[[str | None], str | None]:
    """Build a per-value UDF that defers to a goldenflow series transform.

    Lazy-imports goldenflow + polars to keep the DuckDB extension
    loadable without goldenflow installed (only matters when the
    UDF is actually called).
    """
    def _udf(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            import polars as pl
            from goldenflow.transforms import get_transform
        except ImportError:
            return value  # fail-open: pass through when goldenflow missing
        info = get_transform(transform_name)
        if info is None:
            return value
        series = pl.Series([value])
        try:
            if info.mode == "series":
                out = info.func(series)
            elif info.mode == "expr":
                # expr-mode transforms take a column name; build a tiny
                # frame, apply, extract.
                df = pl.DataFrame({"v": series})
                out = df.with_columns(info.func("v").alias("v"))["v"]
            else:
                # dataframe-mode -- not applicable to single-value UDFs.
                return value
            result = out[0]
        except Exception as exc:
            logger.warning(
                "goldenflow %s failed on value %r: %s",
                transform_name, value, exc,
            )
            return value
        return None if result is None else str(result)
    return _udf


# Public mapping: DuckDB UDF name -> underlying goldenflow transform.
# Each name matches the dbt-goldensuite macro that consumes it.
_UDF_REGISTRY: dict[str, str] = {
    "goldenflow_normalize_email": "email_normalize",
    "goldenflow_normalize_phone": "phone_e164",
    "goldenflow_normalize_date": "date_iso8601",
    "goldenflow_normalize_name_proper": "name_proper",
    "goldenflow_canonicalize_url": "url_normalize",
    "goldenflow_canonicalize_address": "address_standardize",
    "goldenflow_strip": "strip",
    "goldenflow_whitespace_normalize": "collapse_whitespace",
}


def register_goldenflow_functions(con) -> None:  # noqa: ANN001
    """Register 8 goldenflow transform UDFs on a DuckDB connection.

    Safe to call even when goldenflow isn't installed -- the UDFs
    fail-open and pass through inputs unchanged with a debug log.
    Callers should still `pip install goldenflow` for the UDFs to
    actually transform.
    """
    if not _try_import_goldenflow():
        logger.info(
            "goldenflow not installed; goldenflow_* UDFs registered as "
            "pass-throughs. `pip install goldenflow` to enable.",
        )
    for udf_name, transform_name in _UDF_REGISTRY.items():
        fn = _wrap_series_transform(transform_name)
        con.create_function(
            udf_name, fn,
            ["VARCHAR"], "VARCHAR",
        )
