#!/usr/bin/env python3
"""Regenerate the time-to-first-success fixture. Run once; the OUTPUT is what
ships (`customers.csv` + `ground_truth.csv`, both committed).

This exists so the fixture is not an unexplained blob. It is deterministic --
`generate_with_gt(500, seed=0, shape="realistic")` from the QIS harness, the
same person-shaped generator the nightly quality gate uses -- so anyone can
reproduce the two CSVs byte-for-byte and see exactly what the probe measures.

    python scripts/ttfs/make_fixture.py

`scripts/ttfs_probe.py` never imports this; it reads the committed CSVs.

Ground truth is keyed on INPUT ROW INDEX (0-based, header excluded), because
that is what the pipeline's cluster output uses for `__row_id__`. The probe
asserts that alignment rather than assuming it (`check_row_ids`).
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from itertools import combinations
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent

N_ROWS = 500
SEED = 0
SHAPE = "realistic"


def _load_qis():
    spec = importlib.util.spec_from_file_location(
        "quality_invariant_scale", _ROOT / "scripts" / "quality_invariant_scale.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    qis = _load_qis()
    table, cids = qis.generate_with_gt(N_ROWS, seed=SEED, shape=SHAPE)

    cols = table.column_names
    rows = table.to_pylist()

    input_csv = _HERE / "customers.csv"
    with input_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)

    # Expand the oracle cluster ids to canonical duplicate pairs.
    members: dict[int, list[int]] = {}
    for idx, cid in enumerate(cids.tolist()):
        members.setdefault(int(cid), []).append(idx)
    pairs = sorted(
        (a, b) for ids in members.values() if len(ids) > 1 for a, b in combinations(sorted(ids), 2)
    )

    gt_csv = _HERE / "ground_truth.csv"
    with gt_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id_a", "id_b"])
        writer.writerows(pairs)

    print(f"{input_csv}: {len(rows)} rows, columns={cols}")
    print(
        f"{gt_csv}: {len(pairs)} labelled pairs "
        f"across {sum(1 for v in members.values() if len(v) > 1)} clusters"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
