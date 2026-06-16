"""Diagnose whether zero-config goldenmatch UNDERPERFORMS on this dataset, or the
data is just hard.

The committed `auto` / `auto+fields` rows commit a best-effort RED config
(`stop_reason=BUDGET_ITERATIONS`, `failing_subprofile=blocking`) on the 171 short,
mostly-distinct names. This runs the same `name+type+context` frame at increasing
planning effort (`normal` -> `thinking` -> `einstein`, more iterations + measured
blocking) and reports committed health, stop reason, and resulting F1.

If F1 climbs materially with effort, the default underperforms (fixable). If it
stays flat / RED, the data is genuinely hard for name-only blocking and 0.6 is the
honest zero-config floor -- the lever is the semantic embedder (`emb-st` /
`emb-openai`), which bypasses the controller entirely.

    python erkgbench/diagnose_controller.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import polars as pl

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import metrics  # noqa: E402

DATASET = _BENCH_ROOT / "dataset" / "records.csv"


def _load():
    names, types, contexts, entity_ids, classes = [], [], [], [], []
    with DATASET.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            names.append(row["mention"])
            types.append(row["entity_type"])
            contexts.append(row["context"])
            entity_ids.append(row["entity_id"])
            classes.append(row["failure_class"])
    df = pl.DataFrame({"name": names, "entity_type": types, "context": contexts})
    return df, entity_ids, classes


def _clustering(result) -> list[list[int]]:
    return [
        list(info["members"])
        for info in result.clusters.values()
        if info.get("size", len(info["members"])) > 1
    ]


def main() -> None:
    import goldenmatch as gm
    from goldenmatch.core import autoconfig as _ac

    df, entity_ids, classes = _load()
    print(f"{'effort':>9} {'health':>8} {'stop_reason':>22} {'fail_sub':>10} {'F1':>6} {'abbr':>6} {'synm':>6}")
    for effort in ("normal", "thinking", "einstein"):
        try:
            cfg = gm.auto_configure_df(df, planning_effort=effort, confidence_required=False)
            run = _ac._LAST_CONTROLLER_RUN.get()
            health = stop = fail = "?"
            if run:
                _profile, history = run
                committed = history.pick_committed() if hasattr(history, "pick_committed") else None
                health = getattr(getattr(committed, "profile", None), "health", None) or getattr(
                    committed, "health", "?"
                )
                health = getattr(health, "name", str(health))
                stop = getattr(getattr(history, "stop_reason", None), "name", str(getattr(history, "stop_reason", "?")))
                fail = str(getattr(committed, "failing_sub_profile", "?"))
            result = gm.dedupe_df(df, config=cfg)
            by_class = metrics.score_by_class(entity_ids, classes, _clustering(result))
            o = by_class["__overall__"]
            abbr = by_class.get("abbreviation")
            synm = by_class.get("synonym_brand")
            print(
                f"{effort:>9} {str(health):>8} {str(stop):>22} {str(fail):>10} "
                f"{o.f1:>6.3f} {(abbr.f1 if abbr else 0):>6.3f} {(synm.f1 if synm else 0):>6.3f}"
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic must not crash the lane
            print(f"{effort:>9}  ERROR: {type(exc).__name__}: {str(exc)[:90]}")


if __name__ == "__main__":
    main()
