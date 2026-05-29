"""Snowflake handler functions for goldenmatch UDFs and Stored Procedures.

The ``goldenmatch snowflake init`` CLI registers UDFs with
``HANDLER = 'goldenmatch_udfs.<func>'`` (or, equivalently,
``goldenmatch.snowflake.udfs.<func>``). This module IS that handler
catalog.

## Phase 1: scalar UDFs (this module, working)

Per-string transforms and read-only identity lookups. All run as
pure Snowpark Python UDFs -- no Session required, no writes.

  - ``normalize_email`` / ``normalize_phone`` / ``normalize_date``
  - ``normalize_name_proper`` / ``canonicalize_url`` /
    ``canonicalize_address`` / ``strip`` / ``whitespace_normalize``
  - ``identity_resolve`` / ``identity_view`` / ``identity_history``
  - ``identity_conflicts`` / ``identity_list``

The identity reads open a read-only SQLite ``IdentityStore`` from
the path bundled into the UDF via the ``IMPORTS`` clause. Pass
the empty string as ``db_path`` to pick up the default file
(``identity.db`` at the IMPORTS root).

## Phase 2: Stored Procedures (scaffolded, NotImplementedError)

Operations that need a Snowpark ``Session`` -- because they read
or write Snowflake tables -- are scaffolded with clear
NotImplementedError messages and inline TODO markers. These ship
in a follow-up PR once the Snowflake-native ``MemoryStore`` and
``IdentityStore`` backends land.

  - ``correction_add`` (writes a Correction to MemoryStore)
  - ``scan_table`` / ``health_score`` (run GoldenCheck against a
    Snowflake relation)
  - ``DedupeFull`` / ``DedupeClusters`` / ``DedupePairs`` (run a
    full goldenmatch dedupe against a Snowflake relation)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Polars is shipped in Snowflake's Anaconda channel and bundled into
# the UDF imports anyway -- but we import lazily so the cost is paid
# once per worker process.
_pl: Any = None


def _polars():
    global _pl
    if _pl is None:
        import polars as pl
        _pl = pl
    return _pl


# ---------------------------------------------------------------------------
# Helpers: locate the IMPORTS directory inside a running UDF.
# ---------------------------------------------------------------------------


def _import_dir() -> Path:
    """Resolve the Snowflake IMPORTS stage directory.

    Inside a Snowpark Python UDF, ``sys._xoptions['snowflake_import_directory']``
    points at the unpacked IMPORTS root -- the path that the wheel /
    ``identity.db`` are accessible from. Outside Snowflake (local tests)
    we fall back to a ``GOLDENMATCH_UDF_IMPORTS`` env var, then to the
    package directory.
    """
    snowflake_dir = getattr(sys, "_xoptions", {}).get(
        "snowflake_import_directory"
    )
    if snowflake_dir:
        return Path(snowflake_dir)
    env_dir = os.environ.get("GOLDENMATCH_UDF_IMPORTS")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent


def _resolve_db_path(db_path: str | None) -> str:
    """Pick the SQLite path for an identity-read UDF.

    Empty string + None both fall back to ``identity.db`` at the
    IMPORTS root, which is the convention the CLI install script
    documents in ``snowflake-setup.md``.
    """
    if db_path:
        # Absolute paths are honored as-is; relative paths resolve
        # against the IMPORTS root.
        p = Path(db_path)
        if p.is_absolute():
            return str(p)
        return str(_import_dir() / p)
    return str(_import_dir() / "identity.db")


# ---------------------------------------------------------------------------
# Identity reads -- pure UDFs, read-only against a staged SQLite IdentityStore.
# ---------------------------------------------------------------------------


def _identity_store(db_path: str | None):
    """Open a read-only IdentityStore at the resolved path.

    Snowpark UDFs run sandboxed; the only filesystem access is the
    IMPORTS directory (read-only). The IdentityStore class opens
    SQLite in read-only mode when given an existing file, so this is
    safe.
    """
    from goldenmatch.identity.store import IdentityStore
    return IdentityStore(backend="sqlite", path=_resolve_db_path(db_path))


def identity_resolve(record_id: str, db_path: str) -> dict[str, Any] | None:
    """``goldenmatch.goldenmatch_identity_resolve(record_id, db_path)``.

    Look up a record's current identity. Returns a serializable
    summary or None if the record_id is unknown.
    """
    from goldenmatch.identity.query import find_by_record
    store = _identity_store(db_path)
    try:
        view = find_by_record(store, record_id)
        return view.to_dict() if view is not None else None
    finally:
        store.close()


def identity_view(entity_id: str, db_path: str) -> dict[str, Any] | None:
    """``goldenmatch.goldenmatch_identity_view(entity_id, db_path)``.

    Full IdentityView JSON for an entity_id.
    """
    from goldenmatch.identity.query import get_entity
    store = _identity_store(db_path)
    try:
        view = get_entity(store, entity_id)
        return view.to_dict() if view is not None else None
    finally:
        store.close()


def identity_history(entity_id: str, db_path: str) -> list[dict[str, Any]]:
    """``goldenmatch.goldenmatch_identity_history(entity_id, db_path)``.

    Append-only event log for an entity_id. ``history`` returns
    dicts directly -- no further .to_dict() needed.
    """
    from goldenmatch.identity.query import history as _history
    store = _identity_store(db_path)
    try:
        return _history(store, entity_id)
    finally:
        store.close()


def identity_conflicts(dataset: str, db_path: str) -> list[dict[str, Any]]:
    """``goldenmatch.goldenmatch_identity_conflicts(dataset, db_path)``.

    Conflict edges in a dataset. ``find_conflicts`` returns dicts
    directly.
    """
    from goldenmatch.identity.query import find_conflicts
    store = _identity_store(db_path)
    try:
        return find_conflicts(store, dataset=(dataset or None))
    finally:
        store.close()


def identity_list(dataset: str, status: str, db_path: str) -> list[dict[str, Any]]:
    """``goldenmatch.goldenmatch_identity_list(dataset, status, db_path)``.

    List identities, optionally filtered. ``list_entities`` returns
    dicts directly.
    """
    from goldenmatch.identity.query import list_entities
    store = _identity_store(db_path)
    try:
        return list_entities(
            store,
            dataset=(dataset or None),
            status=(status or None),
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# GoldenFlow transforms -- pure scalar UDFs, string -> string.
# ---------------------------------------------------------------------------


def _series_apply(transform_callable, value: str | None) -> str | None:
    """Apply a Polars ``Series -> Series`` goldenflow transform to a single
    value. The Polars round-trip is the price we pay for keeping the
    handler bit-identical with the dbt + DuckDB transform path."""
    if value is None:
        return None
    pl = _polars()
    series = pl.Series([value])
    out = transform_callable(series)
    result = out[0]
    return None if result is None else str(result)


def _expr_apply(expr_callable, value: str | None,
                col_name: str = "_v") -> str | None:
    """Apply a Polars ``column-name -> Expr`` goldenflow transform to a
    single value. Builds a 1-row DataFrame, projects through the Expr,
    pulls back the only cell."""
    if value is None:
        return None
    pl = _polars()
    df = pl.DataFrame({col_name: [value]})
    out = df.select(expr_callable(col_name).alias("_o"))
    result = out["_o"][0]
    return None if result is None else str(result)


def normalize_email(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_normalize_email(s)``.

    Lowercases, strips, drops +tags, normalizes Gmail aliases.
    """
    from goldenflow.transforms.email import email_normalize
    return _series_apply(email_normalize, s)


def normalize_phone(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_normalize_phone(s)``.

    E.164-normalize a phone number. Returns the input unchanged
    when ``phonenumbers`` can't parse it.
    """
    from goldenflow.transforms.phone import phone_e164
    return _series_apply(phone_e164, s)


def normalize_date(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_normalize_date(s)``.

    Parse + emit ISO-8601 ``YYYY-MM-DD``. Returns the input unchanged
    when the parser can't agree on a date.
    """
    from goldenflow.transforms.dates import date_iso8601
    return _series_apply(date_iso8601, s)


def normalize_name_proper(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_normalize_name_proper(s)``.

    Proper-case a person name via ``goldenflow.transforms.names.name_proper``.
    For the full strip-titles -> strip-suffixes -> proper-case
    composition, run ``goldenflow.transform_df`` out-of-band with a
    GoldenFlowConfig -- the UDF stays focused on the case-fix step
    that's the actual matchkey-side normalization.
    """
    from goldenflow.transforms.names import name_proper
    return _series_apply(name_proper, s)


def canonicalize_url(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_canonicalize_url(s)``.

    Lowercases scheme + host, strips default port, drops trailing
    slashes. Wraps ``goldenflow.transforms.url.url_normalize``.
    """
    from goldenflow.transforms.url import url_normalize
    return _series_apply(url_normalize, s)


def canonicalize_address(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_canonicalize_address(s)``.

    USPS-style address normalization: expand abbreviations,
    standardize unit indicators. Wraps
    ``goldenflow.transforms.address.address_standardize``.
    """
    from goldenflow.transforms.address import address_standardize
    return _expr_apply(address_standardize, s)


def strip(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_strip(s)``.

    Strip leading + trailing whitespace.
    """
    if s is None:
        return None
    return s.strip()


def whitespace_normalize(s: str | None) -> str | None:
    """``goldenmatch.goldenflow_whitespace_normalize(s)``.

    Collapse all internal whitespace runs to single spaces, strip
    edges. Idempotent.
    """
    if s is None:
        return None
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# Phase 2: Stored Procedures -- scaffolds with clear TODOs.
# ---------------------------------------------------------------------------


def correction_add(decision: str, dataset: str, memory_path: str,
                   args_json: str) -> str:
    """Stored Procedure: ``goldenmatch.goldenmatch_correction_add(...)``.

    Write a Correction to MemoryStore. Phase 2 -- requires a
    Snowflake-native MemoryStore backend (writes via Snowpark
    Session, not SQLite-on-stage).

    The fully-loaded path in Phase 2 will:

      1. Receive a ``snowflake.snowpark.Session`` as the first
         implicit argument (Stored Procedures get one for free).
      2. Insert a row into ``<db>.goldenmatch.corrections`` mirroring
         ``goldenmatch.core.memory.store.Correction``.
      3. Return the Correction's UUID7 as the procedure result.

    Tracking issue: see ``docs/snowflake-handlers.md``.
    """
    raise NotImplementedError(
        "correction_add ships in Phase 2 of the Snowflake handler module. "
        "Today, file corrections via the dbt macro on Postgres/DuckDB, "
        "or via REST: POST /api/v1/memory/corrections."
    )


def scan_table(relation_name: str, domain: str) -> str:
    """Stored Procedure: ``goldenmatch.goldencheck_scan_table(...)``.

    Run GoldenCheck against a Snowflake relation. Phase 2 -- needs
    a Snowpark Session to read the table into a Polars frame for
    ``goldencheck.engine.scanner.scan_file`` (which currently
    expects a file path).

    Tracking issue: ``docs/snowflake-handlers.md``.
    """
    raise NotImplementedError(
        "scan_table ships in Phase 2. Run `goldencheck scan <export>` "
        "out-of-band against a UNLOADed parquet for now."
    )


def health_score(relation_name: str) -> float:
    """Stored Procedure: ``goldenmatch.goldencheck_health_score(...)``.

    Phase 2 -- wraps ``DatasetProfile.health_score`` after profiling
    the relation. Same Session requirement as ``scan_table``.
    """
    raise NotImplementedError(
        "health_score ships in Phase 2."
    )


class DedupeFull:
    """Stored Procedure handler: ``goldenmatch_dedupe_full``.

    Phase 2 -- reads the input relation into a Polars frame via
    Snowpark, calls ``goldenmatch.dedupe_df(df, config)``, writes
    the golden output back as the procedure result table.

    Tracking issue: ``docs/snowflake-handlers.md``.
    """

    def process(self, input_table: str, config_json: str):  # noqa: D401
        raise NotImplementedError(
            "DedupeFull ships in Phase 2 of the Snowflake handler module. "
            "Today, run dedupe out-of-band: `pip install goldenmatch[snowflake]` "
            "then `from goldenmatch import dedupe_df`."
        )


class DedupeClusters:
    """Stored Procedure handler: ``goldenmatch_dedupe_clusters``.

    Phase 2 -- emits ``(cluster_id, member_id, score)`` rows per
    cluster member instead of one golden row per cluster.
    """

    def process(self, input_table: str, config_json: str):  # noqa: D401
        raise NotImplementedError(
            "DedupeClusters ships in Phase 2."
        )


class DedupePairs:
    """Stored Procedure handler: ``goldenmatch_dedupe_pairs``.

    Phase 2 -- emits the raw scored pairs above the configured
    threshold, for downstream review queue or audit-trail use.
    """

    def process(self, input_table: str, config_json: str):  # noqa: D401
        raise NotImplementedError(
            "DedupePairs ships in Phase 2."
        )


__all__ = [
    # Phase 1 (working).
    "identity_resolve", "identity_view", "identity_history",
    "identity_conflicts", "identity_list",
    "normalize_email", "normalize_phone", "normalize_date",
    "normalize_name_proper", "canonicalize_url", "canonicalize_address",
    "strip", "whitespace_normalize",
    # Phase 2 (scaffolded).
    "correction_add", "scan_table", "health_score",
    "DedupeFull", "DedupeClusters", "DedupePairs",
]


# A top-level shim so the CLI catalog's ``HANDLER = 'goldenmatch_udfs.<func>'``
# resolves whether the wheel is installed as ``goldenmatch`` or unpacked
# under that legacy module name from the IMPORTS stage. The setup script
# adds a ``goldenmatch_udfs.py`` symlink/copy that re-exports these.
