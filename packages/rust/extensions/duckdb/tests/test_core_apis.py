"""Tests for the core-API parity UDFs in ``core_apis.py``.

Each test registers the UDFs on a DuckDB connection, runs the UDF on a
small fixture, and asserts the JSON result shape + key values AGAINST
what the underlying goldenmatch core function returns directly -- so it's
real parity, not a tautology.
"""
from __future__ import annotations

import dataclasses
import json

import duckdb
import pytest
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


# ── profile / threshold / domain ───────────────────────────────────────────


class TestProfileTable:
    def test_matches_core(self, con):
        con.sql("""
            CREATE TABLE prof_t AS SELECT * FROM (VALUES
                ('a@x.com', 'A'),
                ('a@x.com', 'A'),
                ('b@y.com', 'B')
            ) AS t(email, name)
        """)
        result = json.loads(
            con.sql("SELECT goldenmatch_profile_table('prof_t')").fetchone()[0]
        )
        # Parity against the core function on the same data.
        import polars as pl
        from goldenmatch import profile_dataframe
        expected = profile_dataframe(
            pl.DataFrame({"email": ["a@x.com", "a@x.com", "b@y.com"],
                          "name": ["A", "A", "B"]})
        )
        assert result["total_rows"] == expected["total_rows"] == 3
        assert result["total_columns"] == expected["total_columns"] == 2
        assert result["duplicate_row_count"] == expected["duplicate_row_count"]


class TestSuggestThreshold:
    def test_bimodal_matches_core(self, con):
        scores = [0.05, 0.1, 0.08, 0.12, 0.9, 0.92, 0.88, 0.95]
        result = con.sql(
            "SELECT goldenmatch_suggest_threshold(?)",
            params=[json.dumps(scores)],
        ).fetchone()[0]
        from goldenmatch import suggest_threshold
        assert result == pytest.approx(suggest_threshold(scores))
        assert result is not None

    def test_too_few_returns_null(self, con):
        result = con.sql(
            "SELECT goldenmatch_suggest_threshold('[0.5]')"
        ).fetchone()[0]
        assert result is None


class TestDetectDomain:
    def test_matches_core(self, con):
        cols = ["product_title", "brand", "sku", "price"]
        result = json.loads(con.sql(
            "SELECT goldenmatch_detect_domain(?)", params=[json.dumps(cols)],
        ).fetchone()[0])
        from goldenmatch.core.domain import detect_domain
        expected = dataclasses.asdict(detect_domain(cols))
        assert result["name"] == expected["name"] == "product"
        assert result["confidence"] == pytest.approx(expected["confidence"])


class TestExtractFeatures:
    def test_product_matches_core(self, con):
        text = "Sony DSC-T77 Black 10MP Camera"
        result = json.loads(con.sql(
            "SELECT goldenmatch_extract_features(?, 'product')", params=[text],
        ).fetchone()[0])
        from goldenmatch.core.domain import extract_product_features
        expected = dataclasses.asdict(extract_product_features(text))
        assert result["brand"] == expected["brand"] == "Sony"
        assert result["color"] == expected["color"]

    def test_software_matches_core(self, con):
        text = "Microsoft Office 2010 Professional Win"
        result = json.loads(con.sql(
            "SELECT goldenmatch_extract_features(?, 'software')", params=[text],
        ).fetchone()[0])
        from goldenmatch.core.domain import extract_software_features
        expected = dataclasses.asdict(extract_software_features(text))
        assert result["version"] == expected["version"] == "2010"
        assert result["edition"] == expected["edition"] == "pro"

    def test_biblio_matches_core(self, con):
        text = "A Study on Entity Resolution 2019"
        result = json.loads(con.sql(
            "SELECT goldenmatch_extract_features(?, 'biblio')", params=[text],
        ).fetchone()[0])
        from goldenmatch.core.domain import extract_biblio_features
        assert result == extract_biblio_features(text)
        assert result["year"] == "2019"

    def test_unknown_kind_returns_error(self, con):
        result = json.loads(con.sql(
            "SELECT goldenmatch_extract_features('x', 'bogus')"
        ).fetchone()[0])
        assert "error" in result


# ── evaluate / compare_clusters ─────────────────────────────────────────────


class TestEvaluate:
    def test_pairs_matches_core(self, con):
        predicted = [[0, 1, 0.9], [2, 3, 0.8]]
        gt = [[0, 1], [4, 5]]
        result = json.loads(con.sql(
            "SELECT goldenmatch_evaluate(?, ?)",
            params=[json.dumps(predicted), json.dumps(gt)],
        ).fetchone()[0])
        from goldenmatch import evaluate_pairs
        expected = evaluate_pairs(
            [(0, 1, 0.9), (2, 3, 0.8)], {(0, 1), (4, 5)}
        ).summary()
        assert result == expected
        assert result["tp"] == 1 and result["fp"] == 1 and result["fn"] == 1

    def test_clusters_matches_core(self, con):
        clusters = {"0": {"members": [0, 1]}, "1": {"members": [2, 3, 4]}}
        gt = [[0, 1], [2, 3]]
        result = json.loads(con.sql(
            "SELECT goldenmatch_evaluate(?, ?)",
            params=[json.dumps(clusters), json.dumps(gt)],
        ).fetchone()[0])
        from goldenmatch import evaluate_clusters
        expected = evaluate_clusters(
            {0: {"members": [0, 1]}, 1: {"members": [2, 3, 4]}}, {(0, 1), (2, 3)}
        ).summary()
        assert result == expected


class TestCompareClusters:
    def test_matches_core(self, con):
        a = {"0": {"members": [0, 1]}, "1": {"members": [2, 3]}}
        b = {"0": {"members": [0, 1, 2, 3]}}
        result = json.loads(con.sql(
            "SELECT goldenmatch_compare_clusters(?, ?)",
            params=[json.dumps(a), json.dumps(b)],
        ).fetchone()[0])
        from goldenmatch import compare_clusters
        expected = compare_clusters(
            {0: {"members": [0, 1]}, 1: {"members": [2, 3]}},
            {0: {"members": [0, 1, 2, 3]}},
        ).summary()
        assert result == expected
        assert result["merged"] == 2


# ── validate / autofix / anomalies ──────────────────────────────────────────


class TestValidateTable:
    def test_quarantine_matches_core(self, con):
        con.sql("""
            CREATE TABLE val_t AS SELECT * FROM (VALUES
                ('a@x.com'),
                ('not-an-email'),
                ('b@y.com')
            ) AS t(email)
        """)
        rules = [{
            "column": "email", "rule_type": "format",
            "params": {"type": "email"}, "action": "quarantine",
        }]
        result = json.loads(con.sql(
            "SELECT goldenmatch_validate_table('val_t', ?)",
            params=[json.dumps(rules)],
        ).fetchone()[0])

        import polars as pl
        from goldenmatch.core.validate import ValidationRule, validate_dataframe
        valid_df, quarantine_df, report = validate_dataframe(
            pl.DataFrame({"email": ["a@x.com", "not-an-email", "b@y.com"]}),
            [ValidationRule(column="email", rule_type="format",
                            params={"type": "email"}, action="quarantine")],
        )
        assert result["valid_rows"] == valid_df.height == 2
        assert result["quarantine_rows"] == quarantine_df.height == 1
        assert result["report"][0]["failed"] == report[0]["failed"] == 1


class TestAutofixTable:
    def test_matches_core(self, con):
        con.sql("""
            CREATE TABLE fix_t AS SELECT * FROM (VALUES
                ('  John  ', 'a@x.com'),
                ('NULL', 'b@y.com')
            ) AS t(name, email)
        """)
        result = json.loads(
            con.sql("SELECT goldenmatch_autofix_table('fix_t')").fetchone()[0]
        )
        import polars as pl
        from goldenmatch import auto_fix_dataframe
        fixed_df, fixes = auto_fix_dataframe(
            pl.DataFrame({"name": ["  John  ", "NULL"],
                          "email": ["a@x.com", "b@y.com"]})
        )
        assert result["fixed_rows"] == fixed_df.height
        assert {f["fix"] for f in result["fixes"]} == {f["fix"] for f in fixes}
        # Trim-whitespace fix should have run.
        assert any(f["fix"] == "trim_whitespace" for f in result["fixes"])


class TestDetectAnomalies:
    def test_matches_core(self, con):
        con.sql("""
            CREATE TABLE anom_t AS SELECT * FROM (VALUES
                ('test@test.com'),
                ('real@example.org')
            ) AS t(email)
        """)
        result = json.loads(con.sql(
            "SELECT goldenmatch_detect_anomalies('anom_t', 'medium')"
        ).fetchone()[0])
        import polars as pl
        from goldenmatch import detect_anomalies
        expected = detect_anomalies(
            pl.DataFrame({"email": ["test@test.com", "real@example.org"]}),
            sensitivity="medium",
        )
        assert len(result) == len(expected)
        assert any(a["type"] == "fake_email" for a in result)


# ── preflight / postflight ──────────────────────────────────────────────────


_EXACT_CONFIG = json.dumps({
    "matchkeys": [{
        "name": "k", "type": "exact",
        "fields": [{"field": "email", "scorer": "exact"}],
    }],
})


class TestPreflight:
    def test_clean_config_no_errors(self, con):
        con.sql("""
            CREATE TABLE pre_t AS SELECT * FROM (VALUES
                ('a@x.com', 'A'),
                ('a@x.com', 'A'),
                ('b@y.com', 'B')
            ) AS t(email, name)
        """)
        result = json.loads(con.sql(
            "SELECT goldenmatch_preflight('pre_t', ?)", params=[_EXACT_CONFIG],
        ).fetchone()[0])
        import polars as pl
        from goldenmatch.config.schemas import GoldenMatchConfig
        from goldenmatch.core.autoconfig_verify import preflight
        report = preflight(
            pl.DataFrame({"email": ["a@x.com", "a@x.com", "b@y.com"],
                          "name": ["A", "A", "B"]}),
            GoldenMatchConfig.model_validate_json(_EXACT_CONFIG),
        )
        assert result["has_errors"] == report.has_errors is False
        assert "findings" in result

    def test_missing_column_surfaces_finding(self, con):
        con.sql("""
            CREATE TABLE pre_bad AS SELECT * FROM (VALUES ('A')) AS t(name)
        """)
        result = json.loads(con.sql(
            "SELECT goldenmatch_preflight('pre_bad', ?)", params=[_EXACT_CONFIG],
        ).fetchone()[0])
        # email column is absent -> preflight should report it.
        assert result["has_errors"] is True
        assert any(f["severity"] == "error" for f in result["findings"])


class TestPostflight:
    def test_signals_shape(self, con):
        con.sql("""
            CREATE TABLE post_t AS SELECT * FROM (VALUES
                ('a@x.com', 'A'),
                ('a@x.com', 'A'),
                ('b@y.com', 'B'),
                ('c@z.com', 'C')
            ) AS t(email, name)
        """)
        result = json.loads(con.sql(
            "SELECT goldenmatch_postflight('post_t', ?)", params=[_EXACT_CONFIG],
        ).fetchone()[0])
        assert "signals" in result
        # Stable PostflightSignals schema keys.
        for key in ("score_histogram", "threshold_overlap_pct",
                    "total_pairs_scored", "current_threshold"):
            assert key in result["signals"]
        assert "adjustments" in result and "advisories" in result


# ── Fellegi-Sunter train_em / score_probabilistic ───────────────────────────


_PROB_ROWS = [
    {"name": "john smith", "city": "nyc"},
    {"name": "john smith", "city": "nyc"},
    {"name": "jane doe", "city": "la"},
    {"name": "jane doe", "city": "la"},
    {"name": "bob jones", "city": "sf"},
    {"name": "robert jones", "city": "sf"},
]
_PROB_MK = json.dumps({
    "name": "p", "type": "probabilistic",
    "fields": [
        {"field": "name", "scorer": "jaro_winkler", "levels": 3},
        {"field": "city", "scorer": "exact", "levels": 2},
    ],
})


class TestProbabilistic:
    def test_train_then_score_roundtrip(self, con):
        params = json.dumps({"n_sample_pairs": 50, "max_iterations": 10, "seed": 1})
        em_json = con.sql(
            "SELECT goldenmatch_train_em(?, ?, ?)",
            params=[json.dumps(_PROB_ROWS), _PROB_MK, params],
        ).fetchone()[0]
        em = json.loads(em_json)
        # EMResult shape.
        for key in ("m_probs", "u_probs", "match_weights",
                    "converged", "iterations", "proportion_matched"):
            assert key in em
        assert "name" in em["match_weights"] and "city" in em["match_weights"]

        pairs_json = con.sql(
            "SELECT goldenmatch_score_probabilistic(?, ?, ?)",
            params=[json.dumps(_PROB_ROWS), _PROB_MK, em_json],
        ).fetchone()[0]
        pairs = json.loads(pairs_json)
        # Each result is [a, b, score].
        assert all(len(p) == 3 for p in pairs)
        scored = {(p[0], p[1]) for p in pairs}
        # The two identical dup pairs must be linked.
        assert (0, 1) in scored
        assert (2, 3) in scored

    def test_parity_with_core(self, con):
        """The UDF chain must reproduce the core train_em+score result."""
        import polars as pl
        from goldenmatch.config.schemas import MatchkeyConfig
        from goldenmatch.core.probabilistic import (
            EMResult,
            score_probabilistic,
            train_em,
        )
        df = pl.DataFrame(_PROB_ROWS).with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )
        mk = MatchkeyConfig.model_validate_json(_PROB_MK)
        core_em = train_em(df, mk, n_sample_pairs=50, max_iterations=10, seed=1)
        core_pairs = score_probabilistic(df, mk, core_em)

        params = json.dumps({"n_sample_pairs": 50, "max_iterations": 10, "seed": 1})
        em_json = con.sql(
            "SELECT goldenmatch_train_em(?, ?, ?)",
            params=[json.dumps(_PROB_ROWS), _PROB_MK, params],
        ).fetchone()[0]
        pairs_json = con.sql(
            "SELECT goldenmatch_score_probabilistic(?, ?, ?)",
            params=[json.dumps(_PROB_ROWS), _PROB_MK, em_json],
        ).fetchone()[0]
        udf_pairs = [tuple(p) for p in json.loads(pairs_json)]

        # Same linked pairs as the in-process core call.
        assert {(a, b) for a, b, _ in udf_pairs} == {
            (a, b) for a, b, _ in core_pairs
        }
        # Reconstructed EMResult round-trips.
        EMResult(**json.loads(em_json))


# ── record fingerprint (Phase 3b: canonical cross-surface id hash) ──────────


class TestRecordFingerprint:
    def test_matches_core(self, con):
        record = {"first": "Alex", "last": "Smith", "email": "a@x.com"}
        udf = con.sql(
            "SELECT goldenmatch_record_fingerprint(?)",
            params=[json.dumps(record)],
        ).fetchone()[0]
        # Parity against the core fingerprint on the same record.
        from goldenmatch.core._hashing import record_fingerprint
        assert udf == record_fingerprint(record)
        assert len(udf) == 64

    def test_matches_pinned_vector(self, con):
        # {"a": "x"} -> sha256(b"a" 0x1f b"s" b"x" 0x1e), independent of impl.
        udf = con.sql(
            "SELECT goldenmatch_record_fingerprint(?)",
            params=[json.dumps({"a": "x"})],
        ).fetchone()[0]
        assert udf == "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"

    def test_drops_underscore_fields(self, con):
        with_meta = con.sql(
            "SELECT goldenmatch_record_fingerprint(?)",
            params=[json.dumps({"a": 1, "__row_id__": 9})],
        ).fetchone()[0]
        without = con.sql(
            "SELECT goldenmatch_record_fingerprint(?)",
            params=[json.dumps({"a": 1})],
        ).fetchone()[0]
        assert with_meta == without

    def test_non_object_is_fail_soft(self, con):
        out = con.sql(
            "SELECT goldenmatch_record_fingerprint(?)", params=["[1,2,3]"]
        ).fetchone()[0]
        assert json.loads(out)["error"]
