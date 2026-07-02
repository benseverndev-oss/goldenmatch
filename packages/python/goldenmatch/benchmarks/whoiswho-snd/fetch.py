"""Fetch + cache the WhoIsWho / OAG-Bench na-v3 SND dataset.

The THUDM toolkit's primary mirror is Baidu netdisk (unscriptable outside China),
but the toolkit itself downloads from AMiner's public LFS over plain HTTPS --
``https://lfs.aminer.cn/misc/ND-data/na-v3/<file>.json`` -- which is what we use.
No auth, byte-range supported.

The corpus is research-use / redistribution-restricted, so it is fetched on
demand into a gitignored ``data/`` dir and NEVER committed (see ``.gitignore``).

Splits (na-v3):
  train:  train_author.json  (name -> {author_id -> [pid...]}  GROUND TRUTH)
          train_pub.json      (pid  -> paper record)
  valid:  sna_valid_raw.json          (name -> [pid...]  the blocking groups)
          sna_valid_pub.json          (pid  -> paper record)
          sna_valid_ground_truth.json (name -> [[pid...], ...]  the true partition)

The valid split is the HEADLINE set (80 names / ~46k papers, ~110 MB) and is
locally scorable -- ``sna_valid_ground_truth.json`` ships the true clusters.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://lfs.aminer.cn/misc/ND-data/na-v3"

DATA_DIR = Path(os.environ.get("WHOISWHO_DATA_DIR", Path(__file__).parent / "data"))

# (split, {logical_name: remote_filename})
SPLITS: dict[str, dict[str, str]] = {
    "train": {
        "ground_truth": "train_author.json",  # name -> {aid -> [pid]}
        "pub": "train_pub.json",
    },
    "valid": {
        "raw": "sna_valid_raw.json",  # name -> [pid]
        "pub": "sna_valid_pub.json",
        "ground_truth": "sna_valid_ground_truth.json",  # name -> [[pid], ...]
    },
}


def _download(url: str, dest: Path, *, chunk: int = 1 << 20) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "goldenmatch-whoiswho-snd/1.0"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https host
        total = int(resp.headers.get("Content-Length", 0))
        got = 0
        with open(tmp, "wb") as fh:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)
                got += len(block)
                if total:
                    pct = 100 * got / total
                    print(f"\r  {dest.name}: {got >> 20} / {total >> 20} MB ({pct:4.1f}%)",
                          end="", file=sys.stderr, flush=True)
    print("", file=sys.stderr)
    tmp.replace(dest)


def ensure_file(split: str, logical: str, *, data_dir: Path | None = None) -> Path:
    """Return a local path to one split file, downloading + caching if absent."""
    root = Path(data_dir) if data_dir else DATA_DIR
    fname = SPLITS[split][logical]
    dest = root / "na-v3" / fname
    if not dest.exists() or dest.stat().st_size == 0:
        _download(f"{BASE_URL}/{fname}", dest)
    return dest


def load_split(split: str, *, data_dir: Path | None = None) -> dict[str, dict]:
    """Download (if needed) and json-load every file for a split.

    Returns ``{logical_name: parsed_json}`` e.g.
    ``{"raw": {...}, "pub": {...}, "ground_truth": {...}}``.
    """
    out: dict[str, dict] = {}
    for logical in SPLITS[split]:
        path = ensure_file(split, logical, data_dir=data_dir)
        with open(path, encoding="utf-8") as fh:
            out[logical] = json.load(fh)
    return out


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    which = sys.argv[1] if len(sys.argv) > 1 else "valid"
    print(f"Fetching WhoIsWho na-v3 split={which} into {DATA_DIR} ...", file=sys.stderr)
    for logical in SPLITS[which]:
        p = ensure_file(which, logical)
        print(f"  {logical:14s} -> {p}  ({p.stat().st_size >> 20} MB)")
