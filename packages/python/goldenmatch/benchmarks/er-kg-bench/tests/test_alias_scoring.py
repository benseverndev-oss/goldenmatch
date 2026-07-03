"""SP-moat scoring: canon-mapped ranked list -> equivalence-class hit."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.stark_adapter import _apply_id_map  # noqa: E402
from erkgbench.stark_metrics import metrics  # noqa: E402


def test_id_map_canon_then_hit_on_any_alias():
    # retrieved cluster ordinals; ord2canon maps ords 10,11 -> gold entity 1
    ranked = _apply_id_map([12, 10, 11], {10: 1, 11: 1, 12: 2})
    assert ranked == [2, 1]                                  # dedup first-seen
    m = metrics(ranked, {1})                                 # gold entity 1 retrieved via an alias
    assert m["hit@5"] == 1.0 and m["recall@20"] == 1.0


def test_id_map_none_is_passthrough_dedup():
    assert _apply_id_map([5, 3, 5, 9], None) == [5, 3, 9]    # dedup first-seen, no mapping
