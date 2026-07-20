#!/usr/bin/env python
"""Render the FS out-of-core scale results (a dir of per-datapoint JSON) as a
GitHub step-summary markdown table. Kept out of the workflow YAML so the inline
shell stays trivial."""
from __future__ import annotations

import glob
import json
import os
import sys

COLS = [
    ("rows", "rows"),
    ("mode", "mode"),
    ("completed", "completed"),
    ("streaming_engaged", "streaming"),
    ("peak_rss_sampled_mb", "peak_rss_mb"),
    ("wall_s", "wall_s"),
    ("unique_count", "unique"),
    ("dupes_count", "dupes"),
    ("golden_count", "golden"),
]


def main() -> None:
    d = sys.argv[1] if len(sys.argv) > 1 else "results"
    rows = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(f) as fh:
                rows.append(json.load(fh))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda r: (r.get("mode", ""), r.get("rows", 0)))

    out = ["## FS out-of-core scale validation", ""]
    out.append("| " + " | ".join(h for _, h in COLS) + " |")
    out.append("|" + "|".join("---" for _ in COLS) + "|")
    for r in rows:
        peak = r.get("peak_rss_sampled_mb") or r.get("ru_maxrss_mb") or "-"
        cells = []
        for key, _ in COLS:
            cells.append(str(peak if key == "peak_rss_sampled_mb" else r.get(key, "-")))
        out.append("| " + " | ".join(cells) + " |")
        if r.get("error"):
            out.append(f"|  | ⚠ {r['error']} |||||||||")
    print("\n".join(out))


if __name__ == "__main__":
    main()
