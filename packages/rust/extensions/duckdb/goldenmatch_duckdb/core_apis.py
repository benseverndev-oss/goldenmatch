"""DuckDB UDFs wrapping goldenmatch's function-shaped core APIs.

Brings the DuckDB extension to parity with clean core APIs that were
already in goldenmatch's public surface but not yet exposed in SQL.
These are pure-Python UDFs that import ``goldenmatch`` directly -- they
wrap the named core function, they do not reimplement it.

Conventions mirror ``functions.py``:
- Table-input UDFs read the table via ``con.cursor()`` + ``.pl()`` (the
  same deadlock-avoidance pattern as ``_dedupe_table``) and validate the
  table name with ``_validate_table_name``.
- JSON in / JSON out -- inputs are JSON strings, outputs are
  ``json.dumps(...)`` of the core function's return (dataclasses go
  through ``dataclasses.asdict``; ``EvalResult`` / ``CompareResult`` use
  their ``.summary()`` dict).
- Optional-dep / bad-input failures are fail-soft: the UDF returns a
  ``{"error": ...}`` JSON object rather than raising, so a malformed call
  doesn't abort a whole SQL query (matches ``_correction_add``).

Registered via ``register_core_api_functions(con)``, called from
``functions.register(con)`` alongside the goldenflow registration.
"""
from __future__ import annotations

import dataclasses
import json

import duckdb

from goldenmatch_duckdb.functions import _validate_table_name

# ── Registration ─────────────────────────────────────────────────────────


def register_core_api_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the core-API parity UDFs on a DuckDB connection.

    Mirrors ``register_goldenflow_functions`` -- safe to call once per
    connection from ``functions.register``.
    """
    # Profiling / threshold / domain (scalar + table-in JSON-out)
    con.create_function(
        "goldenmatch_profile_table",
        lambda table_name: _profile_table(con, table_name),
        ["VARCHAR"], "VARCHAR",
    )
    # suggest_threshold legitimately returns None (unimodal / too-few
    # scores) -- null_handling="special" lets the UDF emit SQL NULL.
    con.create_function(
        "goldenmatch_suggest_threshold", _suggest_threshold,
        ["VARCHAR"], "DOUBLE", null_handling="special",
    )
    con.create_function(
        "goldenmatch_detect_domain", _detect_domain,
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_extract_features", _extract_features,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Evaluation / cluster comparison (JSON in / JSON out)
    con.create_function(
        "goldenmatch_evaluate", _evaluate,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_compare_clusters", _compare_clusters,
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Data-quality (table in, JSON out)
    con.create_function(
        "goldenmatch_validate_table",
        lambda table_name, rules_json: _validate_table(con, table_name, rules_json),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_autofix_table",
        lambda table_name: _autofix_table(con, table_name),
        ["VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_detect_anomalies",
        lambda table_name, sensitivity: _detect_anomalies(con, table_name, sensitivity),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # AutoConfig verify (table + config in, JSON out)
    con.create_function(
        "goldenmatch_preflight",
        lambda table_name, config_json: _preflight(con, table_name, config_json),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_postflight",
        lambda table_name, config_json: _postflight(con, table_name, config_json),
        ["VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Fellegi-Sunter probabilistic (JSON in / JSON out)
    con.create_function(
        "goldenmatch_train_em", _train_em,
        # rows_json, matchkey_json, params_json
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )
    con.create_function(
        "goldenmatch_score_probabilistic", _score_probabilistic,
        # rows_json, matchkey_json, em_result_json
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
    )

    # Canonical record fingerprint -- the cross-surface stable record-id hash
    # (same value the Python identity path + the native C ABI produce).
    con.create_function(
        "goldenmatch_record_fingerprint", _record_fingerprint,
        ["VARCHAR"], "VARCHAR",
    )


# ── Helpers ──────────────────────────────────────────────────────────────


def _read_table(con: duckdb.DuckDBPyConnection, table_name: str):
    """Read a DuckDB table into a Polars frame via a cursor.

    Same deadlock-avoidance pattern as ``functions._dedupe_table`` -- a
    UDF cannot query the connection it's running on.
    """
    _validate_table_name(table_name)
    cursor = con.cursor()
    df = cursor.sql(f"SELECT * FROM {table_name}").pl()
    cursor.close()
    return df


def _parse_json(value: str, default):
    """Parse a JSON string, returning ``default`` for empty/None input."""
    if value is None or value == "":
        return default
    return json.loads(value)


# ── Profiling / threshold / domain ─────────────────────────────────────────


def _profile_table(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Wrap ``profile_dataframe`` -- comprehensive table profile as JSON."""
    from goldenmatch import profile_dataframe
    try:
        df = _read_table(con, table_name)
        report = profile_dataframe(df)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(report, default=str)


def _suggest_threshold(scores_json: str) -> float | None:
    """Wrap ``suggest_threshold`` -- Otsu threshold over a JSON score list.

    Returns NULL (None) when the distribution is unimodal or there are
    too few scores -- the same semantics as the core function.
    """
    from goldenmatch import suggest_threshold
    try:
        scores = _parse_json(scores_json, [])
        scores = [float(s) for s in scores]
    except Exception:  # noqa: BLE001
        return None
    return suggest_threshold(scores)


def _detect_domain(columns_json: str) -> str:
    """Wrap ``detect_domain`` -- domain profile for a JSON list of columns."""
    from goldenmatch.core.domain import detect_domain
    try:
        columns = _parse_json(columns_json, [])
        columns = [str(c) for c in columns]
        profile = detect_domain(columns)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(dataclasses.asdict(profile), default=str)


def _record_fingerprint(record_json: str) -> str:
    """Wrap ``record_fingerprint`` -- the canonical, cross-surface-stable
    SHA-256 fingerprint (64 hex) of a JSON record object. ``__``-prefixed keys
    are dropped. Returns the same value the Python identity path and the native
    C ABI produce for the same record. Fail-soft to ``{"error": ...}``."""
    from goldenmatch.core._hashing import record_fingerprint
    try:
        record = _parse_json(record_json, {})
        if not isinstance(record, dict):
            return json.dumps({"error": "record must be a JSON object"})
        return record_fingerprint(record)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})


def _extract_features(text: str, kind: str) -> str:
    """Wrap the three ``extract_*_features`` extractors.

    ``kind`` selects the extractor: ``"product"`` / ``"electronics"`` ->
    ``extract_product_features``, ``"software"`` ->
    ``extract_software_features``, ``"biblio"`` /
    ``"bibliographic"`` -> ``extract_biblio_features``.
    """
    from goldenmatch.core.domain import (
        extract_biblio_features,
        extract_product_features,
        extract_software_features,
    )
    if text is None:
        return json.dumps({"error": "text is required"})
    k = (kind or "").strip().lower()
    try:
        if k in ("product", "electronics", ""):
            return json.dumps(dataclasses.asdict(extract_product_features(text)))
        if k == "software":
            return json.dumps(dataclasses.asdict(extract_software_features(text)))
        if k in ("biblio", "bibliographic"):
            # extract_biblio_features already returns a plain dict.
            return json.dumps(extract_biblio_features(text))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "error": (
            f"Unknown kind {kind!r}. Use 'product'/'electronics', "
            "'software', or 'biblio'/'bibliographic'."
        ),
    })


# ── Evaluation / cluster comparison ────────────────────────────────────────


def _evaluate(pairs_json: str, ground_truth_json: str) -> str:
    """Wrap ``evaluate_pairs`` / ``evaluate_clusters``.

    The first argument auto-selects the core function by shape:
    - a JSON array (list of ``[a, b, score]`` triples) -> ``evaluate_pairs``
    - a JSON object (``{cluster_id: {"members": [...]}}``) ->
      ``evaluate_clusters``

    ``ground_truth_json`` is a JSON array of ``[a, b]`` pairs. Returns the
    ``EvalResult.summary()`` dict (tp/fp/fn/precision/recall/f1/...).
    """
    from goldenmatch import evaluate_clusters, evaluate_pairs
    try:
        predicted = _parse_json(pairs_json, [])
        gt_raw = _parse_json(ground_truth_json, [])
        ground_truth = {(p[0], p[1]) for p in gt_raw}

        if isinstance(predicted, dict):
            # Cluster shape: {cluster_id: {"members": [...]}}.
            clusters = {int(k): v for k, v in predicted.items()}
            result = evaluate_clusters(clusters, ground_truth)
        else:
            pairs = [(p[0], p[1], float(p[2]) if len(p) > 2 else 1.0) for p in predicted]
            result = evaluate_pairs(pairs, ground_truth)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(result.summary())


def _compare_clusters(a_json: str, b_json: str) -> str:
    """Wrap ``compare_clusters`` -- CCMS comparison of two clusterings.

    Both args are JSON objects of ``{cluster_id: {"members": [...]}}``.
    Returns the ``CompareResult.summary()`` dict (TWI + case counts).
    """
    from goldenmatch import compare_clusters
    try:
        a = {int(k): v for k, v in _parse_json(a_json, {}).items()}
        b = {int(k): v for k, v in _parse_json(b_json, {}).items()}
        result = compare_clusters(a, b)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(result.summary())


# ── Data-quality (validate / autofix / anomalies) ──────────────────────────


def _validate_table(
    con: duckdb.DuckDBPyConnection, table_name: str, rules_json: str,
) -> str:
    """Wrap ``validate_dataframe`` -- run validation rules over a table.

    ``rules_json`` is a JSON array of rule objects matching the
    ``ValidationRule`` dataclass:
    ``{"column": ..., "rule_type": ..., "params": {...}, "action": ...}``.

    Returns a JSON object with the validation report plus row counts and
    the quarantined rows (as records) -- table-shaped JSON, since DuckDB
    UDFs can only return a scalar.
    """
    from goldenmatch.core.validate import ValidationRule, validate_dataframe
    try:
        df = _read_table(con, table_name)
        rules_spec = _parse_json(rules_json, [])
        rules = [
            ValidationRule(
                column=r["column"],
                rule_type=r["rule_type"],
                params=r.get("params", {}),
                action=r.get("action", "flag"),
            )
            for r in rules_spec
        ]
        valid_df, quarantine_df, report = validate_dataframe(df, rules)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "report": report,
        "valid_rows": valid_df.height,
        "quarantine_rows": quarantine_df.height,
        "quarantine": quarantine_df.to_dicts(),
    }, default=str)


def _autofix_table(con: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """Wrap ``auto_fix_dataframe`` -- apply auto-fixes to a table.

    Returns a JSON object: the list of fixes applied + the fixed rows
    (table-shaped JSON).
    """
    from goldenmatch import auto_fix_dataframe
    try:
        df = _read_table(con, table_name)
        fixed_df, fixes = auto_fix_dataframe(df)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "fixes": fixes,
        "fixed_rows": fixed_df.height,
        "rows": fixed_df.to_dicts(),
    }, default=str)


def _detect_anomalies(
    con: duckdb.DuckDBPyConnection, table_name: str, sensitivity: str,
) -> str:
    """Wrap ``detect_anomalies`` -- flag suspicious records in a table.

    ``sensitivity`` is ``"low"`` / ``"medium"`` / ``"high"`` (empty ->
    ``"medium"``). Returns the JSON array of anomaly dicts.
    """
    from goldenmatch import detect_anomalies
    try:
        df = _read_table(con, table_name)
        sens = sensitivity or "medium"
        anomalies = detect_anomalies(df, sensitivity=sens)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(anomalies, default=str)


# ── AutoConfig verify (preflight / postflight) ─────────────────────────────


def _preflight(
    con: duckdb.DuckDBPyConnection, table_name: str, config_json: str,
) -> str:
    """Wrap ``preflight`` -- validate (df, config) before a run.

    ``config_json`` is a full ``GoldenMatchConfig`` JSON. Returns a JSON
    object with ``has_errors``, ``config_was_modified``, and the list of
    findings.
    """
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig_verify import preflight
    try:
        df = _read_table(con, table_name)
        config = GoldenMatchConfig.model_validate_json(config_json)
        report = preflight(df, config)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "has_errors": report.has_errors,
        "config_was_modified": report.config_was_modified,
        "findings": [dataclasses.asdict(f) for f in report.findings],
    }, default=str)


def _postflight(
    con: duckdb.DuckDBPyConnection, table_name: str, config_json: str,
) -> str:
    """Wrap ``postflight`` -- post-run signal report for (df, config).

    ``postflight`` needs ``pair_scores``, which aren't in the table, so we
    derive them SQL-naturally: run ``dedupe_df`` on the table with the
    given config and feed its ``scored_pairs`` to ``postflight``. Returns
    a JSON object with the stable ``signals`` schema plus any adjustments
    and advisories.
    """
    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig_verify import postflight
    try:
        df = _read_table(con, table_name)
        config = GoldenMatchConfig.model_validate_json(config_json)
        result = dedupe_df(df, config=config)
        report = postflight(df, config, pair_scores=result.scored_pairs)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "signals": report.signals,
        "adjustments": [dataclasses.asdict(a) for a in report.adjustments],
        "advisories": report.advisories,
    }, default=str)


# ── Fellegi-Sunter probabilistic ───────────────────────────────────────────


def _build_probabilistic_frame(rows):
    """Build a Polars frame with the ``__row_id__`` column train_em wants.

    ``train_em`` / ``score_probabilistic`` index pairs by ``__row_id__``.
    Rows that already carry one are respected; otherwise we add a 0-based
    row id so the caller can map results back.
    """
    import polars as pl
    df = pl.DataFrame(rows)
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )
    return df


def _train_em(rows_json: str, matchkey_json: str, params_json: str) -> str:
    """Wrap Fellegi-Sunter ``train_em``.

    Args:
        rows_json: JSON array of record objects (a small training set).
        matchkey_json: JSON for a probabilistic ``MatchkeyConfig``
            (``{"name": ..., "type": "probabilistic", "fields": [...]}``).
        params_json: optional JSON object of train_em kwargs
            (``n_sample_pairs``, ``max_iterations``, ``convergence``,
            ``seed``, ``blocking_fields``). Empty -> defaults.

    Returns the ``EMResult`` as JSON (``m_probs`` / ``u_probs`` /
    ``match_weights`` / ``converged`` / ``iterations`` /
    ``proportion_matched``) -- pass it straight to
    ``goldenmatch_score_probabilistic``.
    """
    from goldenmatch.config.schemas import MatchkeyConfig
    from goldenmatch.core.probabilistic import train_em
    try:
        rows = _parse_json(rows_json, [])
        df = _build_probabilistic_frame(rows)
        mk = MatchkeyConfig.model_validate_json(matchkey_json)
        params = _parse_json(params_json, {})
        allowed = {
            "n_sample_pairs", "max_iterations", "convergence",
            "seed", "blocking_fields",
        }
        kwargs = {k: v for k, v in params.items() if k in allowed}
        em = train_em(df, mk, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps(dataclasses.asdict(em), default=str)


def _score_probabilistic(
    rows_json: str, matchkey_json: str, em_result_json: str,
) -> str:
    """Wrap Fellegi-Sunter ``score_probabilistic``.

    Args:
        rows_json: JSON array of record objects (the block to score).
        matchkey_json: JSON for the same probabilistic ``MatchkeyConfig``
            used for training.
        em_result_json: the JSON produced by ``goldenmatch_train_em``.

    Returns a JSON array of ``[row_id_a, row_id_b, score]`` triples for
    pairs above the link threshold.
    """
    from goldenmatch.config.schemas import MatchkeyConfig
    from goldenmatch.core.probabilistic import EMResult, score_probabilistic
    try:
        rows = _parse_json(rows_json, [])
        df = _build_probabilistic_frame(rows)
        mk = MatchkeyConfig.model_validate_json(matchkey_json)
        em = EMResult(**_parse_json(em_result_json, {}))
        pairs = score_probabilistic(df, mk, em)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})
    return json.dumps([[a, b, score] for (a, b, score) in pairs])
