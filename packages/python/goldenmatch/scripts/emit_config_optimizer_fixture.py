"""Emit cross-language parity fixtures for the config optimizer.

Part A -- proposer determinism: the candidate labels GridProposer and
CoordinateDescentProposer generate per round for a fixed base config (no
scoring). The coordinate scorer tuple is pinned WITHOUT qgram (not a TS
scorer) so both sides propose the same candidates.

Part B -- loop end-to-end (objective="f1", proposer="grid"): runs the REAL
`optimize_config` on a margin-verified dataset. The emitter ASSERTS every
pairwise name score sits >= 0.10 away from every swept threshold, so the TS
pipeline (4-decimal scorer parity) must produce identical clusters, hence
identical per-trial f1 and the same best label.

Output: packages/typescript/goldenmatch/tests/parity/fixtures/config-optimizer.json
Run:    .venv/Scripts/python.exe packages/python/goldenmatch/scripts/emit_config_optimizer_fixture.py
"""
from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.config_optimizer import (
    CoordinateDescentProposer,
    GridProposer,
    SearchState,
    optimize_config,
)
from rapidfuzz.distance import JaroWinkler

PINNED_SCORERS = ("token_sort", "ensemble", "levenshtein", "soundex_match")
LOOP_OFFSETS = (0.0, 0.05, -0.25)
BASE_THRESHOLD = 0.85


def edits_base_config() -> GoldenMatchConfig:
    """Same shape as the config-edits fixture base."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="email", scorer="jaro_winkler", weight=0.8),
                ],
            ),
            MatchkeyConfig(
                name="email_exact",
                type="exact",
                fields=[MatchkeyField(field="email")],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[
                BlockingKeyConfig(fields=["email"], transforms=["lowercase"]),
                BlockingKeyConfig(fields=["zip"]),
            ],
        ),
    )


def part_a() -> dict:
    base = edits_base_config()

    grid = GridProposer()
    grid_labels = [label for label, _ in grid.propose(
        SearchState(base_config=base, objective="f1")
    )]

    coord = CoordinateDescentProposer(
        scorers=PINNED_SCORERS,
        blocking_key_adds=(("last_name",),),
    )
    state = SearchState(base_config=base, objective="f1")
    rounds: list[list[str]] = []
    while True:
        cands = coord.propose(state)
        if not cands:
            break
        rounds.append([label for label, _ in cands])

    return {
        "pinned_scorers": list(PINNED_SCORERS),
        "blocking_key_adds": [["last_name"]],
        "grid_labels": grid_labels,
        "coordinate_rounds": rounds,
    }


def part_b() -> dict:
    names = [
        "aaaa bbbb cccc",   # 0  dup pair A (synthetic -> low cross scores)
        "aaaa bbbb cccc",   # 1
        "zzzz yyyy xxxx",   # 2  dup pair B
        "zzzz yyyy xxxx",   # 3
        # borderline pair (4,5): jw = 0.7339, verified in [0.65, 0.75]
        "kathryn weaver",   # 4
        "kirsten meyer",    # 5
    ]
    jw = lambda a, b: JaroWinkler.normalized_similarity(a, b)  # noqa: E731

    # Margin rule: every non-identical pair score must sit >= 0.10 from every
    # swept threshold, so 4-decimal cross-language scorer parity cannot flip a
    # merge decision in any trial. Identical-string dups score exactly 1.0.
    swept = sorted(min(1.0, max(0.0, BASE_THRESHOLD + o)) for o in LOOP_OFFSETS)
    for i, j in combinations(range(len(names)), 2):
        s = jw(names[i], names[j])
        if (i, j) in {(0, 1), (2, 3)}:
            assert s == 1.0, f"dup pair ({i},{j}) must be identical strings"
            continue
        for t in swept:
            assert abs(s - t) >= 0.10, (
                f"pair ({i},{j}) '{names[i]}'/'{names[j]}' score {s:.4f} "
                f"within 0.10 of threshold {t} -- pick different names"
            )
        if (i, j) == (4, 5):
            assert 0.65 <= s <= 0.75, f"borderline pair score {s:.4f} not in [0.65, 0.75]"

    df = pl.DataFrame({
        "name": names,
        "city": ["x"] * len(names),  # one block: every pair gets scored
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="identity",
            type="weighted",
            threshold=BASE_THRESHOLD,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["city"])]),
    )
    gt = {(0, 1), (2, 3)}

    result = optimize_config(
        df,
        base_config=cfg,
        ground_truth=gt,
        objective="f1",
        proposer="grid",
        threshold_offsets=LOOP_OFFSETS,
    )

    return {
        "rows": [{"name": n, "city": "x"} for n in names],
        "base_threshold": BASE_THRESHOLD,
        "offsets": list(LOOP_OFFSETS),
        "ground_truth": [[0, 1], [2, 3]],
        "expected": {
            "trials": [
                {"label": t.label, "score": t.score, "error": t.error}
                for t in result.trials
            ],
            "best_label": result.best_trial.label,
            "rounds": result.rounds,
            "objective": result.objective,
        },
    }


def main() -> None:
    fixture = {"proposers": part_a(), "loop": part_b()}
    out = (
        Path(__file__).resolve().parents[3]
        / "typescript" / "goldenmatch" / "tests" / "parity" / "fixtures"
        / "config-optimizer.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Wrote {out}")
    print("loop trials:", json.dumps(fixture["loop"]["expected"], indent=2))


if __name__ == "__main__":
    main()
