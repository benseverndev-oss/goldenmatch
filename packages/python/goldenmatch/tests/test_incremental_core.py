"""Direct tests for `core.incremental.run_incremental`.

Nothing called this function directly -- it was reached only through the CLI
`incremental` command and the MCP `incremental` tool, which exercise one happy
path between them. That is why the module sat at ~70% while carrying the
row-id-offset arithmetic that keeps the two populations from colliding.

Both matchkey routes are covered here, because they are genuinely different
code: `exact` goes through `find_exact_matches`, everything else through
per-record `match_one`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from goldenmatch.config.loader import load_config
from goldenmatch.core.incremental import run_incremental

EXACT_CFG = (
    "matchkeys:\n"
    "  - name: exact_email\n"
    "    type: exact\n"
    "    fields:\n"
    "      - field: email\n"
)

WEIGHTED_CFG = (
    "matchkeys:\n"
    "  - name: fuzzy_name\n"
    "    comparison: weighted\n"
    "    threshold: 0.5\n"
    "    fields:\n"
    "      - field: name\n"
    "        scorer: jaro_winkler\n"
    "        weight: 1.0\n"
    "blocking:\n"
    "  strategy: static\n"
    "  keys:\n"
    "    - fields: [city]\n"
)


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _cfg(tmp_path: Path, text: str, name: str = "c.yaml"):
    return load_config(_write(tmp_path, name, text))


def test_exact_match_reports_counts_and_offsets_new_row_ids(tmp_path):
    """New ids start at the base height, so the two populations never collide."""
    base = _write(tmp_path, "base.csv", "id,email\n1,a@x.com\n2,b@x.com\n")
    new = _write(tmp_path, "new.csv", "id,email\n9,a@x.com\n")

    out = run_incremental(base, new, _cfg(tmp_path, EXACT_CFG))

    assert out["base_records"] == 2
    assert out["new_records"] == 1
    assert out["matched_to_base"] == 1
    assert out["new_entities"] == 0
    assert out["matches"] == [
        {"new_row_id": 2, "base_row_id": 0, "score": pytest.approx(1.0)}
    ]


def test_an_unmatched_new_record_counts_as_a_new_entity(tmp_path):
    base = _write(tmp_path, "base.csv", "id,email\n1,a@x.com\n")
    new = _write(tmp_path, "new.csv", "id,email\n9,zzz@x.com\n")

    out = run_incremental(base, new, _cfg(tmp_path, EXACT_CFG))

    assert out["matched_to_base"] == 0
    assert out["new_entities"] == 1
    assert out["matches"] == []


def test_new_vs_new_pairs_are_dropped(tmp_path):
    """Only CROSS-source pairs count. Two new records sharing an email match
    each other on the exact key, and that pair must not be reported."""
    base = _write(tmp_path, "base.csv", "id,email\n1,a@x.com\n")
    new = _write(tmp_path, "new.csv", "id,email\n9,dup@x.com\n10,dup@x.com\n")

    out = run_incremental(base, new, _cfg(tmp_path, EXACT_CFG))

    assert out["matches"] == []
    assert out["new_entities"] == 2


def test_weighted_matchkey_runs_the_match_one_route(tmp_path):
    base = _write(tmp_path, "base.csv", "id,name,city\n1,Ann Smith,Leeds\n2,Bob Jones,York\n")
    new = _write(tmp_path, "new.csv", "id,name,city\n9,Ann Smyth,Leeds\n")

    out = run_incremental(base, new, _cfg(tmp_path, WEIGHTED_CFG))

    assert out["matched_to_base"] == 1
    assert len(out["matches"]) == 1
    match = out["matches"][0]
    assert match["new_row_id"] == 2 and match["base_row_id"] == 0
    assert 0.5 < match["score"] < 1.0


def test_threshold_argument_overrides_the_configured_one(tmp_path):
    """The override is applied to every matchkey that HAS a threshold, so a
    near-1.0 cut drops a match the config's 0.5 accepted."""
    base = _write(tmp_path, "base.csv", "id,name,city\n1,Ann Smith,Leeds\n")
    new = _write(tmp_path, "new.csv", "id,name,city\n9,Ann Smyth,Leeds\n")

    loose = run_incremental(base, new, _cfg(tmp_path, WEIGHTED_CFG), threshold=0.5)
    strict = run_incremental(
        base, new, _cfg(tmp_path, WEIGHTED_CFG, "c2.yaml"), threshold=0.999
    )

    assert loose["matched_to_base"] == 1
    assert strict["matched_to_base"] == 0
    assert strict["new_entities"] == 1


def test_base_and_new_may_carry_different_columns(tmp_path):
    """`concat_frames(relaxed=True)` is the old `how="diagonal"`. A plain concat
    raises when the column sets differ, which is what the base/new pair usually
    looks like in practice."""
    base = _write(tmp_path, "base.csv", "id,email,legacy_code\n1,a@x.com,XX\n")
    new = _write(tmp_path, "new.csv", "id,email,new_field\n9,a@x.com,hello\n")

    out = run_incremental(base, new, _cfg(tmp_path, EXACT_CFG))

    assert out["matched_to_base"] == 1


def test_standardization_rules_are_applied_before_matching(tmp_path):
    """Without the `email` standardizer these are different strings, so the
    exact key would not fire."""
    base = _write(tmp_path, "base.csv", "id,email\n1,A@X.COM\n")
    new = _write(tmp_path, "new.csv", "id,email\n9,a@x.com\n")

    plain = run_incremental(base, new, _cfg(tmp_path, EXACT_CFG))
    assert plain["matched_to_base"] == 0

    std = _cfg(
        tmp_path,
        EXACT_CFG + "standardization:\n  rules:\n    email: [email]\n",
        "std.yaml",
    )
    assert run_incremental(base, new, std)["matched_to_base"] == 1


def test_the_best_score_wins_when_two_matchkeys_hit_the_same_pair(tmp_path):
    """Dedup keeps one row per (new, base) pair."""
    two_keys = (
        "matchkeys:\n"
        "  - name: exact_email\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n"
        "  - name: exact_phone\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: phone\n"
    )
    base = _write(tmp_path, "base.csv", "id,email,phone\n1,a@x.com,555\n")
    new = _write(tmp_path, "new.csv", "id,email,phone\n9,a@x.com,555\n")

    out = run_incremental(base, new, _cfg(tmp_path, two_keys))

    assert out["total_pairs"] == 1
    assert len(out["matches"]) == 1
