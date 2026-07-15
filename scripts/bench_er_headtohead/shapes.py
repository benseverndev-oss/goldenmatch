"""Single source of truth for every fixture-shape fact used by the head-to-head
bench: schema, blocking key, blocking cardinality C (for the projection guard),
and the GoldenMatch hand_built config + Splink settings builders. Every runner
and the generator import from here so no shape fact is defined twice.

IMPORTANT: the ``goldenmatch.config.schemas`` imports live INSIDE each
``_*_gm_hand_built`` builder body, NEVER at module top -- shapes.py is imported
by ``run_splink.py`` and ``generate_fixture.py``, both of which must import
cleanly WITHOUT dragging goldenmatch into ``sys.modules`` (run_splink is designed
to skip when splink/GM are absent; the generator needs only numpy/pyarrow). A
guard test (``test_shapes_import_does_not_drag_goldenmatch``) enforces this.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Shape:
    name: str
    columns: list[str]
    blocking_fields: list[str]  # composite bucket key for GM hand_built
    blocking_cardinality: int  # C: distinct block count (fixed-cardinality key)
    # builders are attached below to avoid importing goldenmatch/splink at module load
    gm_hand_built: Callable  # (threshold) -> GoldenMatchConfig
    splink_settings: Callable  # (s: dict) -> (SettingsCreator, training_rules)


# ---------------------------------------------------------------------------
# person shape (extracted VERBATIM from run_goldenmatch.py + run_splink.py)
# ---------------------------------------------------------------------------
def _person_gm_hand_built(threshold: float):
    """GoldenMatch hand_built config for the person shape -- the EXACT config
    that was inline in run_goldenmatch.py (bucket, n_buckets=256, postcode
    blocking, first_name/surname/dob weighted jaro_winkler, rerank=False)."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        backend="bucket",
        n_buckets=256,
        blocking=BlockingConfig(
            max_block_size=5000,
            skip_oversized=False,  # rely on bucket scorer's hot-block split
            keys=[BlockingKeyConfig(fields=["postcode"], transforms=["strip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="person",
                type="weighted",
                threshold=threshold,
                rerank=False,  # no cross-encoder -> no HuggingFace download
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.3, transforms=["lowercase"]),
                    MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.4, transforms=["lowercase"]),
                    MatchkeyField(field="dob", scorer="jaro_winkler", weight=0.3),
                ],
            )
        ],
    )


def _person_splink_settings(s):
    """Splink settings for the person shape (person fixture columns:
    record_id, first_name, surname, dob, postcode, city). Copied verbatim from
    run_splink.py::_default_person_settings; already ``s``-dict-driven so it
    imports no splink symbols at module load."""
    SettingsCreator = s["SettingsCreator"]
    block_on = s["block_on"]
    cl = s["cl"]
    settings = SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="record_id",
        blocking_rules_to_generate_predictions=[
            block_on("surname", "substr(dob, 1, 4)"),
            block_on("first_name", "substr(dob, 1, 4)"),
            block_on("postcode"),
        ],
        comparisons=[
            cl.JaroWinklerAtThresholds("first_name", [0.9, 0.7]),
            cl.JaroWinklerAtThresholds("surname", [0.9, 0.7]),
            cl.DamerauLevenshteinAtThresholds("dob", [1, 2]),
            cl.DamerauLevenshteinAtThresholds("postcode", [1, 2]),
            cl.ExactMatch("city"),
        ],
    )
    training_rules = [block_on("surname", "dob"), block_on("first_name", "dob")]
    return settings, training_rules


# ---------------------------------------------------------------------------
# Shape registry (person first; biblio appended in Task 2).
# ---------------------------------------------------------------------------
SHAPES: dict[str, Shape] = {
    "person": Shape(
        name="person",
        columns=["record_id", "first_name", "surname", "dob", "postcode", "city"],
        blocking_fields=["postcode"],
        blocking_cardinality=200_000,
        gm_hand_built=_person_gm_hand_built,
        splink_settings=_person_splink_settings,
    ),
}
