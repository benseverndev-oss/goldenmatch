"""Precision ratchet for identity-layer detection.

The corpus in ``test_layers.py`` was authored alongside the detector's
semantics, so it cannot measure precision -- it encodes the assumptions it
tests. ``layers_precision_corpus.json`` is labelled from each schema's NAMING
CONVENTION instead, independent of how the detector scores it, and this module
pins the result.

**The floors below are a ratchet, not a target.** They record what the detector
does TODAY, and both are now at 1.0 on this corpus. Specificity started at 56%
-- it invented a party on nearly half of all single-entity tables -- and the
lineage/audit/aggregate stop-list took it to 100%. Sensitivity started at 71%,
held back by field-type ``name_hints`` suppressing party qualifiers outright,
and declaring those parties as pack roles took it to 100%. The floors exist so
neither can quietly get worse while other work lands; lowering one is a
regression that needs a reason in the diff.

A corpus both floors sit at 1.0 on has stopped discriminating, which is a
reason to grow it rather than to celebrate -- ``test_layers_fk_groundtruth.py``
scores the same detector against labels nobody here wrote, and is at 92%.

Both directions are pinned deliberately. A negatives-only corpus is scored 100%
by a detector that never splits anything, so specificity is only meaningful
reported beside sensitivity -- if a fix for the false-splitting kills real
multi-party detection, the positives catch it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from infermap import detect_identity_layers

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

from layers_precision import run  # noqa: E402

#: Current measured behaviour. See the module docstring: floors, not goals.
#:
#: Specificity is pinned at 1.0 deliberately. Having reached "never invents a
#: party on a single-entity table", ANY regression is worth failing on -- this
#: is the property that decides whether per-block configuration (#2575) can
#: trust a segmentation at all.
MIN_SPECIFICITY = 1.0
MIN_SENSITIVITY_PARTITION = 1.0


def _summary():
    return run(detect_identity_layers)["summary"]


def test_specificity_does_not_regress():
    """False parties on single-entity tables -- what would mis-partition #2575."""
    s = _summary()
    assert s["specificity"] >= MIN_SPECIFICITY, (
        f"specificity fell to {s['specificity']:.0%}; "
        f"new false splits: {s['false_party_cases']}"
    )


def test_partition_sensitivity_does_not_regress():
    """Guards against 'fixing' false splits by never splitting at all."""
    s = _summary()
    assert s["sensitivity_partition"] >= MIN_SENSITIVITY_PARTITION, (
        f"partition sensitivity fell to {s['sensitivity_partition']:.0%}; "
        f"now wrong: {s['wrong_partition_cases']}"
    )


def test_corpus_has_both_halves():
    """A one-sided corpus measures nothing -- neither number stands alone."""
    s = _summary()
    assert s["n_negatives"] >= 10
    assert s["n_positives"] >= 5


def test_table_name_prefix_is_not_a_second_party():
    """The one negative class the detector already handles; pin it.

    Every column repeating the table's own name is a single entity, not a
    party per column-prefix.
    """
    summary = run(detect_identity_layers)
    table_prefix = [
        c for c in summary["negatives"] if c["class"] == "table_prefix"
    ]
    assert table_prefix
    assert all(c["ok"] for c in table_prefix)
