"""Gate + unit tests for the per-section docs-consistency contract.

``test_sections_conform`` mirrors the CI ``check_docs_sections.py`` run (the real
docs must pass). The rest unit-test the pure logic -- title sentence-casing and
canonical page ordering -- so a bad rule is caught here, not only by staring at
the sidebar.
"""
from __future__ import annotations

import check_docs_sections as s


def test_sections_conform():
    problems = s.check()
    assert problems == [], "docs-site sections violate the canonical contract:\n" + "\n".join(problems)


# --- title sentence-case --------------------------------------------------

def test_title_ok_sentence_case():
    for good in [
        "GoldenMatch overview",       # brand first word
        "Config matrix",
        "Blocking strategies",
        "MCP server",                 # acronym stays capped, word does not
        "GoldenMatch API quick reference",
        "Interactive TUI",
        "REST API",
        "Migrating to v2.0",          # digit-bearing version token
        "v1 vs v2 at a glance",       # digit-led first word
        "Amortized Bayesian ER (exploratory)",  # allow-listed proper noun + acronym
        "Native acceleration & deep profiling",
        "Data-quality–aware matching",
        "Config suggestions (the healing loop)",
    ]:
        assert s.title_violations(good) == [], f"false positive on {good!r}"


def test_title_flags_title_case():
    for bad in ["Blocking Strategies", "Domain Packs", "Identity Graph",
                "Learning Memory", "Reference Data", "MCP Server", "ER Agent"]:
        assert s.title_violations(bad), f"missed Title-Case in {bad!r}"


def test_title_flags_lowercase_first_word():
    assert s.title_violations("blocking strategies")


# --- canonical ordering ---------------------------------------------------

def test_order_reference_band_tail_in_fixed_order():
    got = ["overview", "checks", "config-matrix", "recipes", "cli", "native", "integrations"]
    assert s.expected_order(got) == got


def test_order_concept_pages_precede_reference_band():
    # a concept page authored AFTER config-matrix must sort BEFORE it.
    got = ["overview", "analyzers", "config-matrix", "cross-run", "native"]
    assert s.expected_order(got) == ["overview", "analyzers", "cross-run", "config-matrix", "native"]


def test_order_overview_forced_first():
    got = ["mapping", "config-matrix", "overview", "recipes"]
    assert s.expected_order(got)[0] == "overview"


def test_order_concept_pages_keep_authored_relative_order():
    got = ["overview", "zebra", "alpha", "config-matrix"]
    assert s.expected_order(got) == ["overview", "zebra", "alpha", "config-matrix"]
