"""Fair-metric answer canonicalization: dates/times/standalone-number-words compare equal across
formats, WITHOUT making distinct answers collide. Pure, no LLM. Spec: 2026-06-29-stage2a-...-design.md"""
from __future__ import annotations

from erkgbench.qa_e2e import metrics
from erkgbench.qa_e2e.metrics import answer_match


def test_date_formats_canonicalize_equal():
    # the three date phrasings all normalize to the same ISO token sequence
    for a, b in [
        ("11 February 1929", "February 11, 1929"),
        ("11 February 1929", "1929-02-11"),
        ("February 11, 1929", "1929-02-11"),
    ]:
        assert metrics._normalize(a) == metrics._normalize(b), (a, b)


def test_date_distinct_years_still_differ():
    assert metrics._normalize("1928") != metrics._normalize("11 February 1929")
    # same month/day, different year must NOT collide
    assert metrics._normalize("11 February 1928") != metrics._normalize("11 February 1929")


def test_bare_year_not_forced_to_match_full_date():
    # gold = full date, pred mentions only the year -> incomplete, must NOT match (containment)
    assert answer_match("the year was 1929", "11 February 1929") == 0.0


def test_time_formats_canonicalize_equal():
    for a, b in [("5am", "5 a.m."), ("5am", "5 AM"), ("5pm", "5 p.m.")]:
        assert metrics._normalize(a) == metrics._normalize(b), (a, b)


def test_time_am_pm_distinct():
    assert metrics._normalize("5am") != metrics._normalize("5pm")
