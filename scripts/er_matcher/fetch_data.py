#!/usr/bin/env python3
"""Download + normalize the license-clean ER benchmark datasets into the shapes
the loaders expect, under ``data/er_matcher/raw/`` (gitignored -- NOT committed).

Reproducible data acquisition for the bundled sources in ``sources.yaml``:
  - FEBRL synthetic person (MPL-1.1)         -> raw/febrl/febrl_sample.csv
  - Leipzig CC-BY product/citation benchmarks -> raw/leipzig/<name>/{tableA,tableB,mapping}.csv

Only the license-clean BUNDLED sources are fetched here. The fetch-only EVAL
sources (Magellan cite-only, NCVR real-PII) are deliberately NOT downloaded by
this script -- they are pulled by the eval harness (a later sub-project) behind
``GOLDENMATCH_ALLOW_FETCH``, never bundled.

Stdlib only (urllib/zipfile/csv) -- no pandas/recordlinkage dependency. The
source files are latin-1 with dataset-specific mapping columns and (for Scholar)
a UTF-8 BOM; this normalizer re-encodes to UTF-8 and renames columns to the
loaders' contract (``id,<fields>`` tables + ``idA,idB`` mapping; a shared
blocking field across the two tables).

Usage:
  python scripts/er_matcher/fetch_data.py --raw-dir data/er_matcher/raw
  python scripts/er_matcher/fetch_data.py --only abt_buy febrl   # subset
  python scripts/er_matcher/fetch_data.py --febrl dataset1       # smaller FEBRL
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

_UA = {"User-Agent": "goldenmatch-er-matcher-fetch/1.0"}

# FEBRL: single-file org+dup style (rec-<N>-org / rec-<N>-dup-<k>). Direct raw CSV
# from the recordlinkage repo (no pip dep). dataset3 = 5000 records, heavy
# corruption; dataset1 = 1000 (1 dup each), dataset2 = 5000 (lighter).
_FEBRL_URL = ("https://raw.githubusercontent.com/J535D165/recordlinkage/master/"
              "recordlinkage/datasets/febrl/{name}.csv")

# Leipzig CC-BY two-table benchmarks. `block` is the field name BOTH normalized
# tables will share (used by sources.yaml's block_fields). `rename_a`/`rename_b`
# harmonize per-table column names so the two tables share the blocking field
# (Amazon calls it `title`, Google calls it `name` -> standardize to `name`).
_LEIPZIG = {
    "abt_buy": {
        "url": "https://dbs.uni-leipzig.de/files/datasets/Abt-Buy.zip",
        "table_a": "Abt.csv", "table_b": "Buy.csv",
        "mapping": "abt_buy_perfectMapping.csv", "map_a": "idAbt", "map_b": "idBuy",
        "block": "name", "rename_a": {}, "rename_b": {},
    },
    "amazon_google": {
        "url": "https://dbs.uni-leipzig.de/files/datasets/Amazon-GoogleProducts.zip",
        "table_a": "Amazon.csv", "table_b": "GoogleProducts.csv",
        "mapping": "Amzon_GoogleProducts_perfectMapping.csv",  # sic: "Amzon"
        "map_a": "idAmazon", "map_b": "idGoogleBase",
        "block": "name", "rename_a": {"title": "name"}, "rename_b": {},
    },
    "dblp_acm": {
        "url": "https://dbs.uni-leipzig.de/files/datasets/DBLP-ACM.zip",
        "table_a": "DBLP2.csv", "table_b": "ACM.csv",
        "mapping": "DBLP-ACM_perfectMapping.csv", "map_a": "idDBLP", "map_b": "idACM",
        "block": "title", "rename_a": {}, "rename_b": {},
    },
    "dblp_scholar": {
        "url": "https://dbs.uni-leipzig.de/files/datasets/DBLP-Scholar.zip",
        "table_a": "DBLP1.csv", "table_b": "Scholar.csv",
        "mapping": "DBLP-Scholar_perfectMapping.csv", "map_a": "idDBLP", "map_b": "idScholar",
        "block": "title", "rename_a": {}, "rename_b": {},
    },
}


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (trusted hosts)
        return resp.read()


def _decode(raw: bytes) -> str:
    """Strip a UTF-8 BOM if present, then decode latin-1 (the Leipzig/DBLP files
    are latin-1; Scholar.csv is prefixed with a UTF-8 BOM)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("latin-1")


def _read_rows(text: str, *, skipinitialspace: bool = False) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=skipinitialspace)
    fields = [(f or "").strip() for f in (reader.fieldnames or [])]
    rows = list(reader)
    return fields, rows


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# --- pure normalization helpers (unit-tested, no network) --------------------
def normalize_table(fields: list[str], rows: list[dict], rename: dict[str, str],
                    block: str) -> tuple[list[str], list[dict]]:
    """Rename a source table's columns and verify it carries an `id` column and
    the shared blocking field. Returns (header, rows) in the loader's
    `id,<fields...>` contract. Pure -- raises ValueError on a schema mismatch."""
    if "id" not in fields:
        raise ValueError(f"table missing an id column; header={fields}")
    header = [rename.get(c, c) for c in fields]
    out_rows = [{rename.get(k, k): v for k, v in r.items()} for r in rows]
    if block not in header:
        raise ValueError(f"blocking field {block!r} missing after rename; header={header}")
    return header, out_rows


def normalize_mapping(rows: list[dict], map_a: str, map_b: str) -> list[dict]:
    """Rename a gold-mapping file's dataset-specific id columns (e.g.
    `idAbt`/`idBuy`) to the loader's `idA`/`idB`. Pure."""
    if rows and (map_a not in rows[0] or map_b not in rows[0]):
        raise ValueError(f"mapping missing {map_a}/{map_b}; header={list(rows[0])}")
    return [{"idA": r[map_a], "idB": r[map_b]} for r in rows]


def fetch_febrl(raw_dir: Path, which: str = "dataset3") -> int:
    """Download a FEBRL dataset and write raw/febrl/febrl_sample.csv (utf-8,
    header rec_id,<fields>). The raw file has leading spaces after commas."""
    out = raw_dir / "febrl" / "febrl_sample.csv"
    text = _decode(_download(_FEBRL_URL.format(name=which)))
    fields, rows = _read_rows(text, skipinitialspace=True)
    if "rec_id" not in fields:
        raise SystemExit(f"FEBRL: expected a rec_id column, got {fields}")
    _write_csv(out, fields, rows)
    print(f"[febrl] {which}: {len(rows)} records -> {out}")
    return len(rows)


def fetch_leipzig(raw_dir: Path, name: str, cfg: dict) -> int:
    """Download+normalize one Leipzig two-table dataset into
    raw/leipzig/<name>/{tableA,tableB,mapping}.csv (utf-8, loader contract)."""
    out_dir = raw_dir / "leipzig" / name
    zf = zipfile.ZipFile(io.BytesIO(_download(cfg["url"])))
    names = {Path(n).name: n for n in zf.namelist()}

    def _load(member: str) -> tuple[list[str], list[dict]]:
        if member not in names:
            raise SystemExit(f"{name}: {member} not in zip ({sorted(names)})")
        return _read_rows(_decode(zf.read(names[member])))

    def _norm(member: str, rename: dict) -> tuple[list[str], list[dict]]:
        fields, rows = _load(member)
        try:
            return normalize_table(fields, rows, rename, cfg["block"])
        except ValueError as e:
            raise SystemExit(f"{name}/{member}: {e}") from e

    a_hdr, a_rows = _norm(cfg["table_a"], cfg["rename_a"])
    b_hdr, b_rows = _norm(cfg["table_b"], cfg["rename_b"])
    _write_csv(out_dir / "tableA.csv", a_hdr, a_rows)
    _write_csv(out_dir / "tableB.csv", b_hdr, b_rows)

    _, m_rows = _load(cfg["mapping"])
    try:
        mapping = normalize_mapping(m_rows, cfg["map_a"], cfg["map_b"])
    except ValueError as e:
        raise SystemExit(f"{name}/mapping: {e}") from e
    _write_csv(out_dir / "mapping.csv", ["idA", "idB"], mapping)
    print(f"[leipzig] {name}: A={len(a_rows)} B={len(b_rows)} pairs={len(mapping)} "
          f"block={cfg['block']!r} -> {out_dir}")
    return len(mapping)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch + normalize license-clean ER benchmark data.")
    ap.add_argument("--raw-dir", type=Path, default=Path("data/er_matcher/raw"))
    ap.add_argument("--febrl", default="dataset3", choices=["dataset1", "dataset2", "dataset3"])
    ap.add_argument("--only", nargs="*", default=None,
                    help="Subset of {febrl, abt_buy, amazon_google, dblp_acm, dblp_scholar}.")
    args = ap.parse_args(argv)

    want = set(args.only) if args.only else {"febrl", *(_LEIPZIG)}
    if "febrl" in want:
        fetch_febrl(args.raw_dir, args.febrl)
    for name, cfg in _LEIPZIG.items():
        if name in want:
            fetch_leipzig(args.raw_dir, name, cfg)
    print(f"[done] raw data under {args.raw_dir}  "
          f"(reminder: update sources.yaml block_fields to match: "
          f"abt_buy/amazon_google -> name, dblp_* -> title)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
