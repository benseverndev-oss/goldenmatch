"""Generate parity fixtures for cross-language Learning Memory tests.

Determinism: every UUID, every timestamp, every value is fixed. No
``datetime.now()``, no random UUIDs. Re-running this script must produce
byte-identical JSON; ``--rebuild-db`` rebuilds the SQLite fixture.

Outputs (under ``fixtures/`` next to this script):

  * ``memory_corrections.json`` -- 12 corrections covering every source,
    both decisions, full-hash + empty-hash cases, dataset scoping, and an
    ambiguous re-anchor case.
  * ``memory.db`` -- SQLite produced via ``MemoryStore(backend="sqlite")``,
    same 12 corrections.
  * ``memory_apply_inputs.json`` -- frozen apply-outcome golden: input rows,
    matchkey fields, scored pairs, plus the expected ``(adjusted, stats)``
    output computed by ``apply_corrections``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Import path: the package lives one level up from this file's parents (3).
HERE = Path(__file__).resolve()
PKG_ROOT = HERE.parents[3]  # packages/python/goldenmatch
sys.path.insert(0, str(PKG_ROOT))

# NB: deliberately bypass `goldenmatch/__init__.py`, which transitively
# imports polars-using modules. Polars has a known DLL-hang on this dev
# machine (see package CLAUDE.md). Import store.py directly from its path
# via importlib so the generator runs offline. The corrections module is
# hand-rolled below for the same reason.
import importlib.util as _import_util  # noqa: E402

_STORE_PATH = PKG_ROOT / "goldenmatch" / "core" / "memory" / "store.py"
_spec = _import_util.spec_from_file_location("_gm_store", _STORE_PATH)
assert _spec is not None and _spec.loader is not None
_store_mod = _import_util.module_from_spec(_spec)
sys.modules["_gm_store"] = _store_mod
_spec.loader.exec_module(_store_mod)

Correction = _store_mod.Correction
MemoryStore = _store_mod.MemoryStore
_canon_pair = _store_mod._canon_pair

FIXTURE_DIR = HERE.parent / "fixtures"
BASE_TS = datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)


def uuid(i: int) -> str:
    """Pinned sequential UUIDs ``00000000-0000-0000-0000-00000000000{i}``."""
    return f"00000000-0000-0000-0000-{i:012x}"


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def field_hash(row_a_vals: tuple, row_b_vals: tuple) -> str:
    return sha16("|".join(str(v) for v in row_a_vals + row_b_vals))


def record_hash_of(row: dict) -> str:
    cols = sorted(c for c in row if c != "__row_id__")
    return sha16("|".join(str(row[c]) for c in cols))


# Frozen df for the apply-outcome fixture. Schema: name, zip, __row_id__.
APPLY_ROWS = [
    {"__row_id__": 0, "name": "Acme Corp", "zip": "10001"},
    {"__row_id__": 1, "name": "Acme LLC", "zip": "10001"},
    {"__row_id__": 2, "name": "Beta Inc", "zip": "20002"},
    {"__row_id__": 3, "name": "Beta Inc", "zip": "20002"},
    {"__row_id__": 4, "name": "Gamma Co", "zip": "30003"},
]
APPLY_MATCHKEY_FIELDS = ["name", "zip"]
APPLY_DATASET = "parity_test"


def _row_field_vals(rid: int, fields: list[str]) -> tuple:
    row = next(r for r in APPLY_ROWS if r["__row_id__"] == rid)
    return tuple(row[f] for f in fields)


def _record_hash_for_rid(rid: int) -> str:
    row = next(r for r in APPLY_ROWS if r["__row_id__"] == rid)
    return record_hash_of(row)


def make_corrections() -> list[Correction]:
    """12 corrections, deterministic, covering parity invariants."""
    # Pre-compute hashes for the apply fixture.
    fh_0_1 = field_hash(_row_field_vals(0, APPLY_MATCHKEY_FIELDS),
                         _row_field_vals(1, APPLY_MATCHKEY_FIELDS))
    rh_0_1 = f"{_record_hash_for_rid(0)}:{_record_hash_for_rid(1)}"

    fh_0_4 = field_hash(_row_field_vals(0, APPLY_MATCHKEY_FIELDS),
                         _row_field_vals(4, APPLY_MATCHKEY_FIELDS))
    rh_0_4 = f"{_record_hash_for_rid(0)}:{_record_hash_for_rid(4)}"

    # Stale: hash of row 2/4 content but content has been "edited" -- we lie
    # about field_hash so dual-hash check fails -> stale.
    fh_2_4_stale = sha16("Beta Inc|20002|OLD VALUE|99999")  # arbitrary
    rh_2_4 = f"{_record_hash_for_rid(2)}:{_record_hash_for_rid(4)}"

    # Ambiguous: id_a=99 is gone; record_hash of "Beta Inc|20002" maps to
    # BOTH row 2 and row 3 -> stale_ambiguous.
    rh_beta = _record_hash_for_rid(2)  # same as rid 3
    rh_gamma = _record_hash_for_rid(4)
    rh_ambig = f"{rh_beta}:{rh_gamma}"
    fh_ambig = field_hash(("Beta Inc", "20002"), ("Gamma Co", "30003"))

    corrections: list[Correction] = [
        # 1. steward + reject + full hash + parity_test dataset (applies, rows 0,1)
        Correction(
            id=uuid(1), id_a=0, id_b=1,
            decision="reject", source="steward", trust=1.0,
            field_hash=fh_0_1, record_hash=rh_0_1,
            original_score=0.92, matchkey_name="identity",
            reason="merged separate companies",
            dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=1),
        ),
        # 2. api + approve + full hash (applies, rows 0,4)
        Correction(
            id=uuid(2), id_a=0, id_b=4,
            decision="approve", source="api", trust=0.5,
            field_hash=fh_0_4, record_hash=rh_0_4,
            original_score=0.40, matchkey_name="identity",
            reason=None, dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=2),
        ),
        # 3. boost + approve + ambiguous re-anchor (rows 2 & 3 share record hash)
        Correction(
            id=uuid(3), id_a=99, id_b=100,
            decision="approve", source="boost", trust=1.0,
            field_hash=fh_ambig, record_hash=rh_ambig,
            original_score=0.55, matchkey_name="identity",
            reason="ambiguous case", dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=3),
        ),
        # 4. unmerge + reject + EMPTY hashes -- ids gone -> unanchorable
        Correction(
            id=uuid(4), id_a=500, id_b=501,
            decision="reject", source="unmerge", trust=1.0,
            field_hash="", record_hash="",
            original_score=0.0, matchkey_name=None,
            reason="unmerge action", dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=4),
        ),
        # 5. llm + reject + stale (rows present, content "changed" via mismatched fh)
        Correction(
            id=uuid(5), id_a=2, id_b=4,
            decision="reject", source="llm", trust=0.5,
            field_hash=fh_2_4_stale, record_hash=rh_2_4,
            original_score=0.78, matchkey_name="identity",
            reason="llm second opinion", dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=5),
        ),
        # 6. agent + approve + different dataset (filtered out of apply)
        Correction(
            id=uuid(6), id_a=10, id_b=11,
            decision="approve", source="agent", trust=0.5,
            field_hash=sha16("agent|sample"),
            record_hash=f"{sha16('hashA')}:{sha16('hashB')}",
            original_score=0.83, matchkey_name="identity",
            reason="agent autoclassify", dataset="other_dataset",
            created_at=BASE_TS + timedelta(seconds=6),
        ),
        # 7. steward + approve + dataset=None (global; not in parity_test)
        Correction(
            id=uuid(7), id_a=20, id_b=21,
            decision="approve", source="steward", trust=1.0,
            field_hash=sha16("global|steward"),
            record_hash=f"{sha16('gA')}:{sha16('gB')}",
            original_score=0.91, matchkey_name=None,
            reason=None, dataset=None,
            created_at=BASE_TS + timedelta(seconds=7),
        ),
        # 8. unmerge + reject + empty hashes + global dataset
        Correction(
            id=uuid(8), id_a=30, id_b=31,
            decision="reject", source="unmerge", trust=1.0,
            field_hash="", record_hash="",
            original_score=0.0, matchkey_name=None,
            reason="global unmerge", dataset=None,
            created_at=BASE_TS + timedelta(seconds=8),
        ),
        # 9. boost + reject + parity_test, ids gone, recordHash unanchorable
        # (record_hash refers to content not in df).
        Correction(
            id=uuid(9), id_a=700, id_b=701,
            decision="reject", source="boost", trust=1.0,
            field_hash=sha16("missing|content"),
            record_hash=f"{sha16('missingA')}:{sha16('missingB')}",
            original_score=0.66, matchkey_name="identity",
            reason="boost reject", dataset=APPLY_DATASET,
            created_at=BASE_TS + timedelta(seconds=9),
        ),
        # 10. llm + approve + global dataset
        Correction(
            id=uuid(10), id_a=40, id_b=41,
            decision="approve", source="llm", trust=0.5,
            field_hash=sha16("llm|approve"),
            record_hash=f"{sha16('lA')}:{sha16('lB')}",
            original_score=0.88, matchkey_name="identity",
            reason="llm approve", dataset=None,
            created_at=BASE_TS + timedelta(seconds=10),
        ),
        # 11. api + reject + global dataset
        Correction(
            id=uuid(11), id_a=50, id_b=51,
            decision="reject", source="api", trust=0.5,
            field_hash=sha16("api|reject"),
            record_hash=f"{sha16('aA')}:{sha16('aB')}",
            original_score=0.42, matchkey_name="identity",
            reason="api reject", dataset=None,
            created_at=BASE_TS + timedelta(seconds=11),
        ),
        # 12. steward + approve + same-tier latest-wins (idA=0,idB=1,parity_test
        # would collide with #1; use a distinct pair to keep both rows present
        # in storage. Trust upsert is exercised by the test harness, not here.)
        Correction(
            id=uuid(12), id_a=60, id_b=61,
            decision="approve", source="steward", trust=1.0,
            field_hash=sha16("steward|latest"),
            record_hash=f"{sha16('sA')}:{sha16('sB')}",
            original_score=0.95, matchkey_name="identity",
            reason="steward latest", dataset=None,
            created_at=BASE_TS + timedelta(seconds=12),
        ),
    ]
    return corrections


def correction_to_dict(c: Correction) -> dict:
    """Snake-case JSON wire format. ISO-8601 UTC ``Z`` for created_at.

    Matches the TS ``correctionToJSON`` translator and is the canonical
    cross-language serializer.
    """
    return {
        "id": c.id,
        "id_a": c.id_a,
        "id_b": c.id_b,
        "decision": c.decision,
        "source": c.source,
        "trust": c.trust,
        "field_hash": c.field_hash,
        "record_hash": c.record_hash,
        "original_score": c.original_score,
        "matchkey_name": c.matchkey_name,
        "reason": c.reason,
        "dataset": c.dataset,
        "created_at": c.created_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def write_json(corrections: list[Correction]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = [correction_to_dict(c) for c in corrections]
    path = FIXTURE_DIR / "memory_corrections.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(PKG_ROOT)}")


def write_db(corrections: list[Correction]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = FIXTURE_DIR / "memory.db"
    if db_path.exists():
        db_path.unlink()
    store = MemoryStore(backend="sqlite", path=str(db_path))
    try:
        for c in corrections:
            store.add_correction(c)
    finally:
        store.close()
    print(f"  wrote {db_path.relative_to(PKG_ROOT)}")


# --- Apply-outcome fixture ---------------------------------------------------

# Frozen scored pairs. Cover: applied (0,1 reject -> 0.0), applied (0,4
# approve -> 1.0), stale (2,4 mismatched fh), uncovered (1,2 unchanged).
APPLY_SCORED_PAIRS: list[tuple[int, int, float]] = [
    (0, 1, 0.92),
    (0, 4, 0.40),
    (2, 4, 0.78),
    (1, 2, 0.30),
    (3, 4, 0.20),
]


def _apply_corrections_pure(
    scored_pairs: list[tuple[int, int, float]],
    corrections: list[Correction],
    rows: list[dict],
    matchkey_fields: list[str],
    dataset: str | None,
    reanchor: bool,
) -> tuple[list[tuple[int, int, float]], dict]:
    """Polars-free port of ``apply_corrections``.

    Mirrors ``goldenmatch/core/memory/corrections.py:112-234`` exactly. Used
    by the fixture generator on dev machines where ``import polars`` hangs.
    The Python parity test still exercises the canonical (polars-backed)
    implementation against these fixtures.
    """
    stats = {
        "applied": 0, "stale": 0, "stale_ambiguous": 0,
        "stale_unanchorable": 0, "stale_pairs": [],
        "total_pairs": len(scored_pairs),
    }

    if not rows or "__row_id__" not in rows[0]:
        return list(scored_pairs), stats

    relevant = [c for c in corrections if c.dataset == dataset]
    if not relevant:
        return list(scored_pairs), stats

    # record_hash -> [row_ids]
    hash_to_rids: dict[str, list[int]] = {}
    rid_to_record_hash: dict[int, str] = {}
    for r in rows:
        rh = record_hash_of(r)
        rid = int(r["__row_id__"])
        hash_to_rids.setdefault(rh, []).append(rid)
        rid_to_record_hash[rid] = rh
    current_rids = set(rid_to_record_hash)

    # Resolve corrections
    active: dict[tuple[int, int], tuple[Correction, int, int]] = {}
    for c in relevant:
        if c.id_a in current_rids and c.id_b in current_rids:
            ca, cb = _canon_pair(c.id_a, c.id_b)
            active[(ca, cb)] = (c, ca, cb)
            continue
        if not reanchor:
            stats["stale_unanchorable"] += 1
            stats["stale_pairs"].append([c.id_a, c.id_b])
            continue
        rh = c.record_hash or ""
        if ":" not in rh:
            stats["stale_unanchorable"] += 1
            stats["stale_pairs"].append([c.id_a, c.id_b])
            continue
        ha, hb = rh.split(":", 1)
        cands_a = hash_to_rids.get(ha, []) if ha else []
        cands_b = hash_to_rids.get(hb, []) if hb else []
        if len(cands_a) == 1 and len(cands_b) == 1:
            ca, cb = _canon_pair(cands_a[0], cands_b[0])
            active[(ca, cb)] = (c, ca, cb)
        elif cands_a and cands_b:
            stats["stale_ambiguous"] += 1
            stats["stale_pairs"].append([c.id_a, c.id_b])
        else:
            stats["stale_unanchorable"] += 1
            stats["stale_pairs"].append([c.id_a, c.id_b])

    if not active:
        return list(scored_pairs), stats

    row_by_id = {int(r["__row_id__"]): r for r in rows}
    available = [f for f in matchkey_fields if f in rows[0]]

    adjusted: list[tuple[int, int, float]] = []
    for id_a, id_b, score in scored_pairs:
        ca, cb = _canon_pair(id_a, id_b)
        hit = active.get((ca, cb))
        if hit is None:
            adjusted.append((id_a, id_b, score))
            continue
        c, ra, rb = hit
        if ra not in row_by_id or rb not in row_by_id:
            adjusted.append((id_a, id_b, score))
            stats["stale"] += 1
            stats["stale_pairs"].append([id_a, id_b])
            continue
        vals_a = tuple(row_by_id[ra][f] for f in available)
        vals_b = tuple(row_by_id[rb][f] for f in available)
        curr_fh = field_hash(vals_a, vals_b)
        curr_rh = (
            f"{rid_to_record_hash.get(ra, '')}:"
            f"{rid_to_record_hash.get(rb, '')}"
        )
        hashes_empty = (not c.field_hash) and (not c.record_hash)
        hashes_match = (curr_fh == c.field_hash and curr_rh == c.record_hash)
        if hashes_empty or hashes_match:
            new_score = 1.0 if c.decision == "approve" else 0.0
            adjusted.append((id_a, id_b, new_score))
            stats["applied"] += 1
        else:
            adjusted.append((id_a, id_b, score))
            stats["stale"] += 1
            stats["stale_pairs"].append([id_a, id_b])

    return adjusted, stats


def write_apply_inputs(corrections: list[Correction]) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    adjusted, stats = _apply_corrections_pure(
        APPLY_SCORED_PAIRS, corrections, APPLY_ROWS, APPLY_MATCHKEY_FIELDS,
        dataset=APPLY_DATASET, reanchor=True,
    )

    # Sort stale_pairs for cross-language determinism.
    sorted_stale = sorted(stats["stale_pairs"], key=lambda p: (p[0], p[1]))

    payload = {
        "df": APPLY_ROWS,
        "matchkey_fields": APPLY_MATCHKEY_FIELDS,
        "dataset": APPLY_DATASET,
        "reanchor": True,
        "scored_pairs": [list(p) for p in APPLY_SCORED_PAIRS],
        "expected": {
            "adjusted": [list(p) for p in adjusted],
            "stats": {
                "applied": stats["applied"],
                "stale": stats["stale"],
                "stale_ambiguous": stats["stale_ambiguous"],
                "stale_unanchorable": stats["stale_unanchorable"],
                "stale_pairs": sorted_stale,
                "total_pairs": stats["total_pairs"],
            },
        },
    }
    path = FIXTURE_DIR / "memory_apply_inputs.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(PKG_ROOT)}")
    print(
        f"  apply-outcome: applied={stats['applied']} stale={stats['stale']} "
        f"ambiguous={stats['stale_ambiguous']} "
        f"unanchorable={stats['stale_unanchorable']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild-db", action="store_true",
                    help="Rebuild memory.db (slower; otherwise only JSON).")
    args = ap.parse_args()

    print(f"Generating fixtures in {FIXTURE_DIR.relative_to(PKG_ROOT)}/")
    corrections = make_corrections()
    write_json(corrections)
    write_apply_inputs(corrections)
    if args.rebuild_db:
        write_db(corrections)
    else:
        print("  (skipping memory.db; pass --rebuild-db to regenerate)")
    print("done.")


if __name__ == "__main__":
    main()
