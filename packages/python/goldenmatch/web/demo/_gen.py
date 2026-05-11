"""One-shot: build the demo lineage.json + clusters.csv from data.csv.

Produces realistic field-level scores so the workbench gold-graduation
(≥0.95 brightest, ≥0.85 standard, ≥0.7 ink, <0.7 muted) actually surfaces.

Usage (run once, commit the outputs):
    cd packages/python/goldenmatch/web/demo
    python _gen.py
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import jellyfish
from rapidfuzz import fuzz

HERE = Path(__file__).parent
RUN_NAME = "20260506_120000"

# (cluster_id, [row indices])
CLUSTERS: list[tuple[int, list[int]]] = [
    (1, [0, 1, 2]),    # Maya Patel — 3 close variants
    (2, [3, 4, 5]),    # James O'Connor — phone diverges on 5
    (3, [6, 7, 8]),    # Priya — 8 in Boston (different city)
    (4, [9, 10]),      # Lukas Müller / Mueller
    (5, [11, 12, 13]), # Liu Wei — 13 reorders name + diff city
    (6, [14, 15]),     # Aisha
    (7, [16, 17, 18]), # Diego Reyes — 18 truncated + diff city
    (8, [19, 20]),     # Yusuf
    (9, [21, 22]),     # Sarah Klein — clean duplicate
    (10, [23, 24, 25]),# Akiko — 25 in Sendai
]
SINGLETONS = [26, 27]  # Renato, Mei


def jw(a: str, b: str) -> float:
    return round(jellyfish.jaro_winkler_similarity(a.lower().strip(), b.lower().strip()), 4)


def lev(a: str, b: str) -> float:
    """Normalized Levenshtein similarity."""
    return round(fuzz.ratio(a.lower().strip(), b.lower().strip()) / 100.0, 4)


def diff_type(score: float) -> str:
    if score >= 0.97:
        return "agree"
    if score >= 0.80:
        return "partial"
    return "disagree"


def main() -> None:
    rows: list[dict[str, str]] = []
    with (HERE / "data.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    pairs: list[dict] = []
    for cid, members in CLUSTERS:
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ra, rb = rows[a], rows[b]
                fields = [
                    {
                        "field": "name",
                        "scorer": "jaro_winkler",
                        "value_a": ra["name"],
                        "value_b": rb["name"],
                        "score": jw(ra["name"], rb["name"]),
                        "weight": 0.6,
                    },
                    {
                        "field": "email",
                        "scorer": "levenshtein",
                        "value_a": ra["email"],
                        "value_b": rb["email"],
                        "score": lev(ra["email"], rb["email"]),
                        "weight": 0.3,
                    },
                    {
                        "field": "city",
                        "scorer": "jaro_winkler",
                        "value_a": ra["city"],
                        "value_b": rb["city"],
                        "score": jw(ra["city"], rb["city"]),
                        "weight": 0.1,
                    },
                ]
                for f in fields:
                    f["diff_type"] = diff_type(f["score"])
                composite = round(sum(f["score"] * f["weight"] for f in fields), 4)
                pairs.append({
                    "row_id_a": a,
                    "row_id_b": b,
                    "score": composite,
                    "cluster_id": cid,
                    "fields": fields,
                })

    lineage = {
        "generated_at": datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC).isoformat(),
        "run_name": RUN_NAME,
        "total_pairs": len(pairs),
        "pairs": pairs,
    }
    (HERE / f"{RUN_NAME}_lineage.json").write_text(
        json.dumps(lineage, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Clusters CSV: every row gets a cluster_id (singletons own a unique id).
    next_singleton_id = max(cid for cid, _ in CLUSTERS) + 1
    row_to_cluster: dict[int, int] = {}
    for cid, members in CLUSTERS:
        for m in members:
            row_to_cluster[m] = cid
    for s in SINGLETONS:
        row_to_cluster[s] = next_singleton_id
        next_singleton_id += 1

    with (HERE / f"{RUN_NAME}_clusters.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "cluster_id"])
        for rid in sorted(row_to_cluster):
            w.writerow([rid, row_to_cluster[rid]])

    print(f"wrote {len(pairs)} pairs, {len(row_to_cluster)} cluster assignments")


if __name__ == "__main__":
    main()
