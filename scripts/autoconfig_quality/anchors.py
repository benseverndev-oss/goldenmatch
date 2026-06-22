"""Shared synthetic anchor generators — ONE definition, imported by both the
tests and the quality harness. Bodies lifted verbatim from the test fixtures
(test_quality_gate.gen_labeled, test_autoconfig_multisource._crm_df) and the
package script (repro_issue_715.make_healthcare_df)."""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import polars as pl

# make_healthcare_df lives in the package scripts/ dir (not importable as a
# package); add it to sys.path and re-export, so anchors.py is the one place
# that knows where each shape lives.
_PKG_SCRIPTS = Path(__file__).resolve().parents[2] / "packages/python/goldenmatch/scripts"
if str(_PKG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PKG_SCRIPTS))
from repro_issue_715 import make_healthcare_df  # noqa: E402,F401  (re-export)

# ── person-match anchor (gen_labeled) ──────────────────────────────────────────
_SURN = [
    "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore",
    "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
    "Thompson", "Garcia", "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis",
    "Lee", "Walker", "Hall", "Allen", "Young", "King", "Wright", "Lopez",
]
_FIRST = [
    "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
    "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley", "Parker",
    "Quinn", "Riley", "Sage", "Taylor", "Umi", "Val", "Wren", "Xena", "Yael",
    "Zane", "Avery", "Brook", "Cleo", "Drew",
]


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 3:
        return s
    i = rng.randrange(len(s) - 1)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]  # adjacent-char swap


def gen_labeled(n_entities: int = 400, seed: int = 7) -> tuple[pl.DataFrame, set]:
    """Synthetic records with known ground truth. Each entity = 1 original +
    0-2 typo'd clones (sharing email + zip). Returns (df, ground_truth_pairs)
    where pairs are (row_index, row_index) for rows of the same true entity."""
    rng = random.Random(seed)
    n_zip = max(1, n_entities // 2)
    tagged: list[tuple[dict, int]] = []
    for e in range(n_entities):
        f, l = rng.choice(_FIRST), rng.choice(_SURN)
        z = f"{rng.randrange(n_zip):05d}"
        email = f"{f}.{l}.{e}@x.com".lower()
        tagged.append(({"first_name": f, "last_name": l, "email": email, "zip": z}, e))
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            tagged.append(
                ({"first_name": _typo(f, rng), "last_name": l, "email": email, "zip": z}, e)
            )
    rng.shuffle(tagged)
    df = pl.DataFrame([rec for rec, _ in tagged])
    by_entity: dict[int, list[int]] = defaultdict(list)
    for pos, (_, e) in enumerate(tagged):
        by_entity[e].append(pos)
    gt: set = set()
    for positions in by_entity.values():
        for a, b in combinations(sorted(positions), 2):
            gt.add((a, b))
    return df, gt


# ── shared-email CRM anchor (multisource demote-phone / keep-shared-email) ─────
def crm_df() -> pl.DataFrame:
    rows = []
    srcs = ["hubspot", "salesforce", "cvent"]
    for i in range(30):
        s = srcs[i % 3]
        rows.append({
            "source": s,
            "rec_id": f"{s}-{i}",                  # disjoint per source
            "first": f"first{i // 2}",
            "last": f"last{i // 2}",
            "email": f"user{i // 2}@ex.com",       # shared across sources
            "phone": "5551112222" if i < 6 else f"555{i:07d}",
        })
    return pl.DataFrame(rows)
