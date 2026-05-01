"""Tests for goldenmatch-duckdb UDF functions."""
import json

import duckdb
import pytest

from goldenmatch_duckdb.functions import register


@pytest.fixture
def con():
    """Create a DuckDB connection with goldenmatch functions registered."""
    c = duckdb.connect()
    register(c)
    return c


class TestScore:
    def test_jaro_winkler(self, con):
        result = con.sql(
            "SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler')"
        ).fetchone()[0]
        assert isinstance(result, float)
        assert 0.7 < result < 1.0

    def test_exact_match(self, con):
        result = con.sql(
            "SELECT goldenmatch_score('hello', 'hello', 'exact')"
        ).fetchone()[0]
        assert result == 1.0

    def test_exact_no_match(self, con):
        result = con.sql(
            "SELECT goldenmatch_score('hello', 'world', 'exact')"
        ).fetchone()[0]
        assert result == 0.0

    def test_levenshtein(self, con):
        result = con.sql(
            "SELECT goldenmatch_score('kitten', 'sitting', 'levenshtein')"
        ).fetchone()[0]
        assert 0.0 < result < 1.0


class TestScorePair:
    def test_basic(self, con):
        result = con.sql("""
            SELECT goldenmatch_score_pair(
                '{"name": "John Smith", "email": "j@x.com"}',
                '{"name": "Jon Smyth", "email": "j@x.com"}',
                '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
            )
        """).fetchone()[0]
        assert isinstance(result, float)
        assert result > 0.5


class TestExplain:
    def test_basic(self, con):
        result = con.sql("""
            SELECT goldenmatch_explain(
                '{"name": "John Smith", "email": "j@x.com"}',
                '{"name": "Jon Smyth", "email": "j@x.com"}',
                '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
            )
        """).fetchone()[0]
        assert isinstance(result, str)
        assert len(result) > 0


class TestDedupeJson:
    def test_basic(self, con):
        rows = json.dumps([
            {"email": "john@x.com", "name": "John"},
            {"email": "john@x.com", "name": "JOHN"},
            {"email": "jane@y.com", "name": "Jane"},
        ])
        config = json.dumps({"exact": ["email"]})
        result = con.sql(f"""
            SELECT goldenmatch_dedupe('{rows}', '{config}')
        """).fetchone()[0]
        assert isinstance(result, str)
        assert len(result) > 0


class TestDedupeTable:
    def test_basic(self, con):
        con.sql("""
            CREATE TABLE test_customers AS
            SELECT * FROM (VALUES
                ('John', 'john@x.com'),
                ('JOHN', 'john@x.com'),
                ('Jane', 'jane@y.com')
            ) AS t(name, email)
        """)
        result = con.sql("""
            SELECT goldenmatch_dedupe_table('test_customers', '{"exact": ["email"]}')
        """).fetchone()[0]
        assert isinstance(result, str)
        assert len(result) > 0


class TestMatchTables:
    def test_basic(self, con):
        con.sql("""
            CREATE TABLE test_target AS
            SELECT * FROM (VALUES ('John', 'john@x.com')) AS t(name, email)
        """)
        con.sql("""
            CREATE TABLE test_ref AS
            SELECT * FROM (VALUES ('JOHN SMITH', 'john@x.com'), ('Bob', 'bob@z.com')) AS t(name, email)
        """)
        result = con.sql("""
            SELECT goldenmatch_match_tables('test_target', 'test_ref', '{"exact": ["email"]}')
        """).fetchone()[0]
        assert isinstance(result, str)


class TestPipeline:
    def test_configure(self, con):
        result = con.sql("""
            SELECT gm_configure('test_job', '{"exact": ["email"]}')
        """).fetchone()[0]
        assert "configured" in result

    def test_full_pipeline(self, con):
        # Create test data
        con.sql("""
            CREATE TABLE pipeline_customers AS
            SELECT * FROM (VALUES
                ('John', 'john@x.com'),
                ('JOHN', 'john@x.com'),
                ('Jane', 'jane@y.com')
            ) AS t(name, email)
        """)

        # Configure (must fetch to execute the UDF)
        con.sql("SELECT gm_configure('pipe_test', '{\"exact\": [\"email\"]}')").fetchone()

        # Run
        result = con.sql("SELECT gm_run('pipe_test', 'pipeline_customers')").fetchone()[0]
        stats = json.loads(result)
        assert "total_records" in stats

        # List jobs
        jobs_json = con.sql("SELECT gm_jobs()").fetchone()[0]
        jobs = json.loads(jobs_json)
        assert len(jobs) >= 1
        assert any(j["name"] == "pipe_test" for j in jobs)

        # Get golden records
        golden_json = con.sql("SELECT gm_golden('pipe_test')").fetchone()[0]
        assert isinstance(golden_json, str)

        # Drop
        result = con.sql("SELECT gm_drop('pipe_test')").fetchone()[0]
        assert "dropped" in result

        # Verify dropped
        jobs_json = con.sql("SELECT gm_jobs()").fetchone()[0]
        jobs = json.loads(jobs_json)
        assert not any(j["name"] == "pipe_test" for j in jobs)
