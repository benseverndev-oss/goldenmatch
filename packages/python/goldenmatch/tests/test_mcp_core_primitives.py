"""Tests for the 5 core-primitive MCP tools (reverse-parity with the TS surface).

These wrap functions goldenmatch already had; the gap was that an AGENT could not
reach them over MCP without loading a run into session state first. So the tests
assert (a) the wire shapes match the TS handlers, (b) they are genuinely STATELESS
(work with no run loaded), and (c) bad input returns a tool error rather than
raising out of the call.
"""

from __future__ import annotations

import pytest
from goldenmatch.mcp.server import (
    _BASE_TOOLS,
    _tool_build_clusters,
    _tool_find_exact_matches,
    _tool_find_fuzzy_matches,
    _tool_score_pair,
    _tool_score_strings,
)

PRIMITIVES = [
    "score_strings",
    "score_pair",
    "find_exact_matches",
    "find_fuzzy_matches",
    "build_clusters",
]


@pytest.fixture
def sample_file(tmp_path):
    """Three rows sharing an email (one differing only by case) + two singletons."""
    p = tmp_path / "people.csv"
    p.write_text(
        "name,email,city\n"
        "Alice Nguyen,alice@x.com,Boston\n"
        "Alice Nguyen,alice@x.com,Boston\n"
        "alice nguyen,ALICE@X.COM,boston\n"
        "Bob Okafor,bob@y.com,Denver\n"
        "Carol Petrov,carol@z.com,Austin\n",
        encoding="utf-8",
    )
    return str(p)


def test_all_five_are_registered():
    names = {t.name for t in _BASE_TOOLS}
    assert set(PRIMITIVES) <= names


def test_every_primitive_declares_its_required_args():
    by_name = {t.name: t for t in _BASE_TOOLS if t.name in PRIMITIVES}
    assert by_name["score_strings"].inputSchema["required"] == ["a", "b"]
    assert by_name["score_pair"].inputSchema["required"] == ["row_a", "row_b"]
    assert by_name["find_exact_matches"].inputSchema["required"] == ["path", "field"]
    assert by_name["find_fuzzy_matches"].inputSchema["required"] == ["path", "field"]
    assert by_name["build_clusters"].inputSchema["required"] == ["path"]


class TestScoreStrings:
    def test_wire_shape_and_identity(self):
        out = _tool_score_strings("alice", "alice", "jaro_winkler")
        assert out == {"scorer": "jaro_winkler", "score": 1.0}

    def test_near_match_scores_between(self):
        out = _tool_score_strings("Alice Nguyen", "alice nguyen", "jaro_winkler")
        assert 0.0 < out["score"] < 1.0

    def test_unknown_scorer_returns_error_not_raise(self):
        out = _tool_score_strings("a", "b", "definitely_not_a_scorer")
        assert "error" in out


class TestScorePair:
    def test_weighted_fields_combine(self):
        out = _tool_score_pair(
            {"name": "Alice Nguyen", "city": "Boston"},
            {"name": "alice nguyen", "city": "boston"},
            [
                {"field": "name", "scorer": "jaro_winkler", "weight": 2.0,
                 "transforms": ["lowercase", "strip"]},
                {"field": "city", "scorer": "exact", "weight": 1.0,
                 "transforms": ["lowercase", "strip"]},
            ],
        )
        assert out == {"score": 1.0, "field_count": 2}

    def test_defaults_applied_when_field_entry_is_bare(self):
        # Only `field` is required; scorer/weight/transforms take TS-parity defaults.
        out = _tool_score_pair({"name": "abc"}, {"name": "abc"}, [{"field": "name"}])
        assert out["field_count"] == 1
        assert out["score"] == pytest.approx(1.0)

    def test_empty_fields_is_an_error_not_a_zero_score(self):
        # Silently returning 0.0 would read as "these records don't match".
        out = _tool_score_pair({"a": 1}, {"a": 1}, [])
        assert "error" in out

    def test_malformed_field_entry_returns_error(self):
        out = _tool_score_pair({"a": "x"}, {"a": "y"}, [{"scorer": "exact"}])
        assert "error" in out


class TestFindMatches:
    def test_exact_pairs_all_three_shared_emails(self, sample_file):
        out = _tool_find_exact_matches(sample_file, "email", None)
        # rows 0,1,2 share an email once the default lowercase transform applies
        assert out["pair_count"] == 3
        assert sorted(p[:2] for p in out["pairs"]) == [[0, 1], [0, 2], [1, 2]]
        assert all(p[2] == 1.0 for p in out["pairs"])

    def test_fuzzy_pairs_are_scored(self, sample_file):
        out = _tool_find_fuzzy_matches(sample_file, "name", "jaro_winkler", 0.85, None)
        assert out["pair_count"] >= 1
        assert all(0.0 <= p[2] <= 1.0 for p in out["pairs"])

    def test_high_threshold_narrows_the_pair_set(self, sample_file):
        loose = _tool_find_fuzzy_matches(sample_file, "name", "jaro_winkler", 0.5, None)
        tight = _tool_find_fuzzy_matches(sample_file, "name", "jaro_winkler", 0.99, None)
        assert tight["pair_count"] <= loose["pair_count"]

    def test_missing_column_is_a_tool_error(self, sample_file):
        out = _tool_find_exact_matches(sample_file, "no_such_column", None)
        assert "error" in out and "no_such_column" in out["error"]

    def test_missing_file_is_a_tool_error(self, tmp_path):
        out = _tool_find_exact_matches(str(tmp_path / "absent.csv"), "email", None)
        assert "error" in out


class TestBuildClusters:
    def test_groups_the_shared_email_rows(self, sample_file):
        out = _tool_build_clusters(sample_file, ["email"], None, None, None)
        assert out["cluster_count"] >= 1
        sizes = sorted((c["size"] for c in out["clusters"]), reverse=True)
        assert sizes[0] == 3  # the three alice rows land together
        big = next(c for c in out["clusters"] if c["size"] == 3)
        assert set(big) == {"cluster_id", "size", "confidence", "quality", "members"}
        assert sorted(big["members"]) == [0, 1, 2]

    def test_missing_file_is_a_tool_error(self, tmp_path):
        out = _tool_build_clusters(str(tmp_path / "absent.csv"), None, None, None, None)
        assert "error" in out


def test_primitives_are_stateless(sample_file):
    """No run is loaded in this process -- every primitive must still work.

    This is the whole point of the reverse-parity gap: the TS surface let an agent
    reach these directly, while on Python they were unreachable over MCP without
    first establishing session run state.
    """
    assert "error" not in _tool_score_strings("a", "a", "exact")
    assert "error" not in _tool_find_exact_matches(sample_file, "email", None)
    assert "error" not in _tool_build_clusters(sample_file, ["email"], None, None, None)
