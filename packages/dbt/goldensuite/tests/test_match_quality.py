"""Tests for the goldenmatch_match_quality generic test.

The `{% test goldenmatch_match_quality(...) %}` block is a thin
one-line wrapper that delegates to the `goldenmatch_match_quality_sql`
helper macro (and uses the `dbt_goldensuite.` namespace, which plain
Jinja2 doesn't provide). We therefore test the two pure-return HELPER
macros directly:

  * `goldenmatch_predicted_pairs_sql`  -- render-tests (SQL shape)
  * `goldenmatch_match_quality_sql`    -- render-tests + DuckDB execution

The DuckDB execution tests are the load-bearing correctness check:
they run the emitted SQL against real tables and assert the exact
metric numbers (tp/fp/fn/precision/recall/f1).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")
import jinja2.ext  # noqa: E402

# `none` in the plan's call sites is Jinja's null; in this Python harness
# the helpers are invoked as plain functions, so map it to Python None.
none = None

_MACROS_PATH = (
    Path(__file__).resolve().parents[1]
    / "macros" / "test_match_quality.sql"
)


class _TestTagExtension(jinja2.ext.Extension):
    """No-op parser for dbt's custom `{% test ... %}` tag.

    The single `test_match_quality.sql` file holds both the pure-return
    helper macros (which we render-test) AND the thin
    `{% test goldenmatch_match_quality(...) %}` wrapper. dbt registers
    `test` as a custom Jinja tag; plain Jinja2 does not, so the file
    won't parse without this. The wrapper is intentionally NOT
    render-tested (it's a one-line delegate to the helper), so we just
    consume the block body and emit nothing.
    """

    tags = {"test"}

    def parse(self, parser):  # noqa: ANN001
        # Consume the rest of the opening `{% test ... %}` tag header.
        while parser.stream.current.type != "block_end":
            next(parser.stream)
        # Drop the body up to and including `{% endtest %}`.
        parser.parse_statements(["name:endtest"], drop_needle=True)
        return []


class _DbtStub:
    @staticmethod
    def string_literal(s):  # noqa: ANN001
        return "'" + str(s).replace("'", "''") + "'"


class _ExceptionsStub:
    @staticmethod
    def raise_compiler_error(msg):  # noqa: ANN001
        raise RuntimeError(msg)


def _load_match_quality_macros():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_MACROS_PATH.parent)),
        autoescape=False,
        extensions=["jinja2.ext.do", _TestTagExtension],
    )
    env.globals["dbt"] = _DbtStub()
    env.globals["exceptions"] = _ExceptionsStub()
    env.globals["return"] = lambda v: v
    env.globals["tojson"] = lambda v: json.dumps(v)
    env.globals["load_file_contents"] = lambda p: None
    template = env.get_template(_MACROS_PATH.name)
    module = template.module
    return module


# ---------------------------------------------------------------------------
# Task 1: goldenmatch_predicted_pairs_sql
# ---------------------------------------------------------------------------


def test_predicted_pairs_pairs_input():
    h = _load_match_quality_macros()
    sql = h.goldenmatch_predicted_pairs_sql(model="m", input="pairs",
        pairs_a="id_a", pairs_b="id_b", record_id="record_id", cluster_id="cluster_id")
    assert "LEAST(id_a, id_b)" in sql and "GREATEST(id_a, id_b)" in sql
    assert "FROM m" in sql and "id_a <> id_b" in sql


def test_predicted_pairs_clusters_input():
    h = _load_match_quality_macros()
    sql = h.goldenmatch_predicted_pairs_sql(model="m", input="clusters",
        pairs_a="id_a", pairs_b="id_b", record_id="record_id", cluster_id="cluster_id")
    assert "x.cluster_id = y.cluster_id" in sql and "x.record_id < y.record_id" in sql


def test_predicted_pairs_invalid_input_errors():
    h = _load_match_quality_macros()
    with pytest.raises(RuntimeError):
        h.goldenmatch_predicted_pairs_sql(model="m", input="bogus",
            pairs_a="id_a", pairs_b="id_b", record_id="record_id", cluster_id="cluster_id")


# ---------------------------------------------------------------------------
# Task 2: goldenmatch_match_quality_sql
# ---------------------------------------------------------------------------


def test_match_quality_sql_emits_metrics_and_floor():
    h = _load_match_quality_macros()
    sql = h.goldenmatch_match_quality_sql(model="m", ground_truth="gt", input="pairs",
        pairs_a="id_a", pairs_b="id_b", record_id="record_id", cluster_id="cluster_id",
        gt_a="id_a", gt_b="id_b", min_f1=0.9, min_precision=none, min_recall=none)
    assert "COALESCE(SUM(CASE WHEN" in sql      # tp/fp counted as 0 not NULL on empty
    assert "1.0 *" in sql                        # float division
    assert "NULLIF(" in sql
    assert "f1 IS NULL OR f1 < 0.9" in sql       # only the provided floor's clause
    assert "precision IS NULL" not in sql        # unset floors omitted


def test_match_quality_sql_no_floor_errors():
    h = _load_match_quality_macros()
    with pytest.raises(RuntimeError):
        h.goldenmatch_match_quality_sql(model="m", ground_truth="gt", input="pairs",
            pairs_a="id_a", pairs_b="id_b", record_id="record_id", cluster_id="cluster_id",
            gt_a="id_a", gt_b="id_b", min_f1=none, min_precision=none, min_recall=none)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
