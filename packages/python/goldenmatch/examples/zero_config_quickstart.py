"""Zero-config quickstart for goldenmatch v1.8+.

The auto-config controller produces a defensible match/dedupe config
without any manual tuning. Just hand it a DataFrame; it picks the
blocking strategy, scoring weights, threshold, and (when an LLM API key
is available) per-pair LLM scoring on borderline pairs.

What you'll see:
    1. Zero-config dedupe -- controller-derived config + clusters
    2. Zero-config cross-source match -- controller-derived config + matched pairs
    3. Inspecting the controller's audit trail to see which rules fired

Run with: python examples/zero_config_quickstart.py
"""
from __future__ import annotations
import polars as pl
import goldenmatch as gm


def example_dedupe() -> None:
    """Zero-config dedupe on a small bibliographic-shape DataFrame."""
    df = pl.DataFrame({
        "id": ["1", "2", "3", "4", "5", "6"],
        "title": [
            "concurrency in the data warehouse",
            "concurrency in data warehouses",         # near-dup of #1
            "energy efficient indexing on air",
            "energy-efficient indexing on air",        # near-dup of #3
            "neurorule a connectionist approach",
            "an unrelated paper",
        ],
        "authors": [
            "richard taylor", "r. taylor",
            "tomasz imielinski", "t. imielinski",
            "hongjun lu", "anonymous",
        ],
        "year": ["2000", "2000", "1994", "1994", "1995", "2010"],
    })

    print("=== Zero-config dedupe ===")
    result = gm.dedupe_df(df)

    multi_member = sum(1 for c in result.clusters.values() if c["size"] >= 2)
    print(f"  {df.height} input rows -> {len(result.clusters)} clusters "
          f"({multi_member} multi-member)")

    # Inspect what the controller did
    if result.postflight_report and result.postflight_report.controller_history:
        history = result.postflight_report.controller_history
        print(f"  controller iterations: {history.iteration}")
        if history.decisions:
            print(f"  rules fired: {[d.rule_name for d in history.decisions]}")


def example_match() -> None:
    """Zero-config cross-source match between two bibliographic frames."""
    target = pl.DataFrame({
        "id": ["DBLP_1", "DBLP_2", "DBLP_3"],
        "title": [
            "a survey of distributed databases",
            "energy efficient indexing on air",
            "concurrency in the data warehouse",
        ],
        "authors": ["smith jones", "imielinski", "taylor"],
        "year": ["1995", "1994", "2000"],
    })
    reference = pl.DataFrame({
        "id": ["ACM_1", "ACM_2", "ACM_3"],
        "title": [
            "distributed databases survey",          # matches DBLP_1
            "energy efficient indexing on air",       # matches DBLP_2
            "an unrelated paper",
        ],
        "authors": ["smith jones", "tomasz imielinski", "xxx"],
        "year": ["1995", "1994", "2010"],
    })

    print("\n=== Zero-config cross-source match ===")
    result = gm.match_df(target, reference)
    matched_count = result.matched.height if result.matched is not None else 0
    print(f"  {target.height} target x {reference.height} reference "
          f"-> {matched_count} matches")


def example_inspect_audit_trail() -> None:
    """How to read the controller's decisions out of the postflight report."""
    df = pl.DataFrame({
        "id": [str(i) for i in range(40)],
        "title": [f"some paper {i // 4}" for i in range(40)],  # 10 sets of duplicates
        "authors": [f"author {i // 4}" for i in range(40)],
        "year": ["2000"] * 40,
    })
    result = gm.dedupe_df(df)

    print("\n=== Controller audit trail ===")
    if result.postflight_report and result.postflight_report.controller_history:
        history = result.postflight_report.controller_history
        for entry in history.entries:
            health = entry.profile.health().value
            decision = entry.decision.rule_name if entry.decision else "(no rule)"
            print(f"  iter {entry.iteration}: health={health}  decision={decision}")
            if entry.decision:
                print(f"      -> {entry.decision.rationale}")


if __name__ == "__main__":
    example_dedupe()
    example_match()
    example_inspect_audit_trail()
