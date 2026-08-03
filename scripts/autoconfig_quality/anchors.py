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


# ── household hard-negative anchor (off-peak FS link_threshold) ────────────────
# A person shape with NO strong identifier and HOUSEHOLD hard-negatives: distinct
# people who share a surname (family/co-residence) but differ on first_name + dob.
# The shared surname co-blocks them and the FS scorer -- agreeing on surname (and
# often city) while disagreeing on first_name/dob -- scores those NON-match pairs
# just below the true-duplicate pairs. The fixed default link_threshold (0.50)
# therefore OVER-MERGES households; the F1-optimal cutoff sits ABOVE 0.50. This
# is the standing target for the FS threshold-refit work: measured committed 0.50
# F1 ~0.90 (P ~0.82) vs oracle link=0.70 F1 ~0.98 -- a ~+0.078 headroom the
# non-iterated FS path leaves on the table. Realistic (not degenerate): a shape
# with heavier household field-overlap drives F1 at 0.50 far lower (P ~0.03,
# giant surname-collapsed clusters -- an over-merge observable purely from cluster
# shape), so the severity is a knob, not a fixed pathology.
_CITY = ["Springfield", "Riverside", "Franklin", "Clinton", "Georgetown",
         "Salem", "Madison", "Auburn", "Ashland", "Fairview"]
_STREETS = ["Oak St", "Main St", "Elm Ave", "Cedar Rd", "Pine Ln", "Maple Dr",
            "Park Blvd", "Hill Rd", "Lake Ave", "River Rd"]


def _dob(rng: random.Random) -> str:
    return f"{rng.randrange(1940, 2000)}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}"


def gen_household_hardneg(
    n_households: int = 350, seed: int = 41,
) -> tuple[pl.DataFrame, set]:
    """Household hard-negative person shape (see the section comment).

    Each household = 2-3 DISTINCT people sharing a surname; each person = 1
    original + 1-2 typo'd duplicates (mild first-name transposition, small dob
    day-jitter). Ground truth = same-person pairs ONLY; household co-members are
    non-matches that nonetheless co-block and score high. Deterministic per
    ``seed``. Returns ``(df, gt_pairs)`` with ``(row_index, row_index)`` pairs."""
    rng = random.Random(seed)
    tagged: list[tuple[dict, int]] = []
    eid = 0
    for _h in range(n_households):
        surname = rng.choice(_SURN)
        for _m in range(rng.choice([2, 3])):
            first = rng.choice(_FIRST)
            dob = _dob(rng)
            city = rng.choice(_CITY)
            street = f"{rng.randrange(1, 300)} {rng.choice(_STREETS)}"
            tagged.append(({"first_name": first, "surname": surname, "city": city,
                            "street": street, "dob": dob}, eid))
            for _ in range(rng.choice([1, 1, 2])):
                y, m, _d = dob.split("-")
                dup_dob = f"{y}-{m}-{rng.randrange(1, 29):02d}" if rng.random() < 0.5 else dob
                tagged.append(({"first_name": _typo(first, rng), "surname": surname,
                                "city": city, "street": street, "dob": dup_dob}, eid))
            eid += 1
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


def gen_cotenant_hardneg(
    n_addresses: int = 300, seed: int = 29,
) -> tuple[pl.DataFrame, set]:
    """Shared-ADDRESS co-tenant hard-negative shape -- the SEVERE over-merge
    counterpart to ``gen_household_hardneg`` (a MODERATE surname over-merge), with
    a DIFFERENT cause: distinct people with DIFFERENT surnames sharing an
    address (street + city), not a surname.

    Each address = 2-3 DISTINCT people (own first + surname + dob) sharing the
    street+city; each person = 1 original + 1-2 typo'd duplicates. The shared
    address co-blocks the co-tenants and the FS scorer -- agreeing on street +
    city -- places those NON-match pairs above the fixed 0.50 cutoff, so the
    committed config over-merges HARD (measured F1 ~0.41, precision ~0.26; giant
    address-collapsed clusters). Realistic: address alone can't dedupe people,
    which is exactly why co-residence is a classic ER hard negative. The
    threshold-refit loop recovers it (~0.41 -> ~1.00). Ground truth = same-person
    pairs ONLY. Deterministic per ``seed``. Returns ``(df, gt_pairs)``."""
    rng = random.Random(seed)
    tagged: list[tuple[dict, int]] = []
    eid = 0
    for _a in range(n_addresses):
        city = rng.choice(_CITY)
        street = f"{rng.randrange(1, 300)} {rng.choice(_STREETS)}"
        for _m in range(rng.choice([2, 3])):
            first = rng.choice(_FIRST)
            surname = rng.choice(_SURN)
            dob = _dob(rng)
            tagged.append(({"first_name": first, "surname": surname, "city": city,
                            "street": street, "dob": dob}, eid))
            for _ in range(rng.choice([1, 1, 2])):
                tagged.append(({"first_name": _typo(first, rng),
                                "surname": _typo(surname, rng), "city": city,
                                "street": street, "dob": dob}, eid))
            eid += 1
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
