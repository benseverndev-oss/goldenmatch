"""Extract schema-matching cases from the Valentine benchmark into infermap cases.

Valentine data layout (after extracting the Zenodo zip 10.5281/zenodo.5084605):

    <root>/Valentine-datasets/
        Magellan/<pair_name>/<pair_name>_{source,target}.csv
                            /<pair_name>_mapping.json
        ChEMBL/<scenario>/<pair_name>/<pair_name>_{source,target}.csv
                                      /<pair_name>_mapping.json
        OpenData/<scenario>/<pair_name>/...
        TPC-DI/<scenario>/<pair_name>/...

Each *_mapping.json has the form:
    {"matches": [{"source_table", "source_column", "target_table", "target_column"}, ...]}

This script samples at most 100 rows (deterministic, seed=42) from each CSV,
derives expected.json from the mapping, and writes case.json matching the
infermap-bench schema (source block, not provenance; matches manifest.py's
CaseSource dataclass). It is re-runnable and writes nothing outside
`benchmark/cases/valentine/`.

Usage:
    python benchmark/scripts/extract_valentine.py \\
        --valentine-root D:/show_case/_valentine_tmp/valentine_data
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from pathlib import Path

# Deterministic sampling seed used throughout the script.
SEED = 42
MAX_ROWS = 100

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO_ROOT / "benchmark" / "cases" / "valentine"

VALENTINE_URL = "https://github.com/delftdata/valentine"
VALENTINE_LICENSE = "BSD-3-Clause"
VALENTINE_ATTRIBUTION = (
    "Koutras et al., 'Valentine: Evaluating Matching Techniques for Dataset "
    "Discovery', ICDE 2021. Data: Zenodo 10.5281/zenodo.5084605."
)

# Target ~80 cases. Magellan is capped at 7 physical pairs. Rebalance the rest.
QUOTAS = {
    "magellan": 7,
    "chembl": 25,
    "opendata": 25,
    "tpch": 25,
}


def slugify(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
    )


def read_csv_header(csv_path: Path) -> list[str]:
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def count_csv_rows(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)  # minus header


def sample_csv(src: Path, dst: Path, seed: int) -> None:
    """Copy `src` to `dst`, preserving header, sampling at most MAX_ROWS rows.

    Deterministic: uses `random.Random(seed).sample`. If there are <= MAX_ROWS
    data rows, copies everything verbatim.
    """
    with src.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        rows = list(reader)

    if len(rows) > MAX_ROWS:
        rng = random.Random(seed)
        idxs = sorted(rng.sample(range(len(rows)), MAX_ROWS))
        rows = [rows[i] for i in idxs]

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def load_mapping(path: Path) -> list[tuple[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for m in raw.get("matches", []):
        s = m.get("source_column")
        t = m.get("target_column")
        if s is not None and t is not None:
            out.append((str(s), str(t)))
    return out


def build_expected(
    mapping: list[tuple[str, str]],
    src_cols: list[str],
    tgt_cols: list[str],
) -> dict:
    src_set = set(src_cols)
    tgt_set = set(tgt_cols)
    kept: list[dict[str, str]] = []
    seen_src: set[str] = set()
    seen_tgt: set[str] = set()
    for s, t in mapping:
        # Only keep mappings where both columns actually exist in the CSVs,
        # and enforce 1:1 (drop later duplicates if the ground truth is n:m).
        if s in src_set and t in tgt_set and s not in seen_src and t not in seen_tgt:
            kept.append({"source": s, "target": t})
            seen_src.add(s)
            seen_tgt.add(t)
    unmapped_source = sorted(src_set - seen_src)
    unmapped_target = sorted(tgt_set - seen_tgt)
    return {
        "mappings": kept,
        "unmapped_source": unmapped_source,
        "unmapped_target": unmapped_target,
    }


def classify_difficulty(n_src: int, n_tgt: int, n_mapped: int) -> str:
    wider = max(n_src, n_tgt)
    if wider == 0:
        return "hard"
    overlap = n_mapped / wider
    if wider <= 8 and overlap >= 0.8:
        return "easy"
    if overlap >= 0.5:
        return "medium"
    return "hard"


def discover_pairs(root: Path, subdir: str) -> list[tuple[str, Path]]:
    """Return sorted list of (pair_name, leaf_dir) for a dataset subdirectory.

    Magellan: root/Magellan/<pair>/ directly holds the files.
    ChEMBL / OpenData / TPC-DI: root/<subdir>/<scenario>/<pair>/<files>.
    """
    base = root / subdir
    if not base.is_dir():
        return []
    pairs: list[tuple[str, Path]] = []
    if subdir == "Magellan":
        for d in sorted(base.iterdir()):
            if d.is_dir():
                pairs.append((d.name, d))
        return pairs
    for scenario in sorted(base.iterdir()):
        if not scenario.is_dir():
            continue
        for leaf in sorted(scenario.iterdir()):
            if not leaf.is_dir():
                continue
            pair_name = leaf.name
            # Scope qualifier for subcategory context (scenario) in tags only.
            pairs.append((pair_name, leaf))
    return pairs


def find_pair_files(leaf: Path, pair_name: str) -> tuple[Path, Path, Path] | None:
    src = leaf / f"{pair_name}_source.csv"
    tgt = leaf / f"{pair_name}_target.csv"
    mpp = leaf / f"{pair_name}_mapping.json"
    if src.exists() and tgt.exists() and mpp.exists():
        return (src, tgt, mpp)
    return None


def extract_category(
    valentine_root: Path,
    subcategory: str,  # "magellan" | "chembl" | "opendata" | "tpch"
    valentine_subdir: str,  # "Magellan" | "ChEMBL" | "OpenData" | "TPC-DI"
    quota: int,
) -> list[dict]:
    """Extract up to `quota` cases from one Valentine dataset.

    Returns a list of manifest entries (dicts matching CaseRef schema).
    """
    out_dir = OUT_ROOT / subcategory
    # Clean any previous extraction for this subcategory so the script is
    # deterministic and re-runnable.
    if out_dir.exists():
        shutil.rmtree(out_dir)

    pairs = discover_pairs(valentine_root, valentine_subdir)
    if not pairs:
        print(f"  warn: no pairs found under {valentine_subdir}", file=sys.stderr)
        return []

    # Deterministic selection: stride-sample so we get a mix across scenarios.
    if len(pairs) > quota:
        stride = len(pairs) / quota
        chosen_idx = sorted({int(i * stride) for i in range(quota)})
        # If integer collisions reduce count, backfill with the next indices.
        i = 0
        while len(chosen_idx) < quota and i < len(pairs):
            if i not in chosen_idx:
                chosen_idx.append(i)
            i += 1
        chosen_idx = sorted(set(chosen_idx))[:quota]
        pairs = [pairs[i] for i in chosen_idx]

    manifest_entries: list[dict] = []
    skipped = 0

    for idx, (pair_name, leaf) in enumerate(pairs, start=1):
        files = find_pair_files(leaf, pair_name)
        if files is None:
            skipped += 1
            continue
        src_csv, tgt_csv, mapping_json = files

        slug = f"{slugify(pair_name)}_{idx:03d}"
        case_id = f"valentine/{subcategory}/{slug}"
        case_dir = out_dir / slug

        try:
            sample_csv(src_csv, case_dir / "source.csv", SEED)
            sample_csv(tgt_csv, case_dir / "target.csv", SEED + 1)
        except Exception as exc:
            print(f"  skip {case_id}: sample failed — {exc}", file=sys.stderr)
            if case_dir.exists():
                shutil.rmtree(case_dir)
            skipped += 1
            continue

        src_cols = read_csv_header(case_dir / "source.csv")
        tgt_cols = read_csv_header(case_dir / "target.csv")
        if not src_cols or not tgt_cols:
            shutil.rmtree(case_dir)
            skipped += 1
            continue

        mapping = load_mapping(mapping_json)
        expected = build_expected(mapping, src_cols, tgt_cols)

        (case_dir / "expected.json").write_text(
            json.dumps(expected, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Extract scenario (parent dir name) for tagging, where present.
        scenario = leaf.parent.name if valentine_subdir != "Magellan" else "pair"
        difficulty = classify_difficulty(
            len(src_cols), len(tgt_cols), len(expected["mappings"])
        )
        tags = sorted({
            "valentine",
            subcategory,
            slugify(scenario),
        })

        case_json = {
            "id": case_id,
            "category": "valentine",
            "subcategory": subcategory,
            "source": {
                "name": f"valentine-{subcategory}-{pair_name}",
                "url": VALENTINE_URL,
                "license": VALENTINE_LICENSE,
                "attribution": VALENTINE_ATTRIBUTION,
            },
            "tags": tags,
            "expected_difficulty": difficulty,
            "notes": (
                f"Valentine {valentine_subdir}/{scenario}/{pair_name}. "
                f"Sampled to <= {MAX_ROWS} rows (seed={SEED})."
            ),
        }
        (case_dir / "case.json").write_text(
            json.dumps(case_json, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        manifest_entries.append({
            "id": case_id,
            "path": f"cases/valentine/{subcategory}/{slug}",
            "category": "valentine",
            "subcategory": subcategory,
            "source": case_json["source"],
            "tags": tags,
            "expected_difficulty": difficulty,
            "field_counts": {"source": len(src_cols), "target": len(tgt_cols)},
        })

    if skipped:
        print(f"  {subcategory}: skipped {skipped} pairs", file=sys.stderr)
    return manifest_entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--valentine-root",
        default="D:/show_case/_valentine_tmp/valentine_data/Valentine-datasets",
        help="Path to extracted Valentine-datasets directory",
    )
    args = ap.parse_args()
    root = Path(args.valentine_root)
    if not root.is_dir():
        print(f"ERROR: --valentine-root {root} not found", file=sys.stderr)
        return 2

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    all_entries: list[dict] = []
    per_cat: dict[str, int] = {}
    for subcat, subdir in (
        ("magellan", "Magellan"),
        ("chembl", "ChEMBL"),
        ("opendata", "OpenData"),
        ("tpch", "TPC-DI"),
    ):
        quota = QUOTAS[subcat]
        print(f"extracting {subcat} (quota={quota})...")
        entries = extract_category(root, subcat, subdir, quota)
        all_entries.extend(entries)
        per_cat[subcat] = len(entries)

    # Persist a small index for convenience (not required by runner).
    (OUT_ROOT / "_index.json").write_text(
        json.dumps(
            {"count": len(all_entries), "per_subcategory": per_cat},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        f"wrote {len(all_entries)} cases across {len([c for c in per_cat.values() if c])} "
        f"subcategories: {per_cat}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
