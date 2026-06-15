#!/usr/bin/env python3
"""Dogfood: render the REAL goldenmatch engine resolving entities.

End-to-end reproducible pipeline — no hand-built fixtures:

    generate noisy people  ->  goldenmatch dedupe  ->  scored pairs (the match
    graph)  ->  edge list  ->  graph-layout  ->  frames  ->  video

Run from this directory (needs `goldenmatch` + `polars` installed):

    python examples/dogfood_goldenmatch.py            # writes dogfood_pairs.csv
    python export_graph_layout.py from-pairs dogfood_pairs.csv \\
        --a a --b b --score score -o dogfood_edges.tsv

  Flat 2D layout video (the force-directed frames):

    cargo run --release -- --input dogfood_edges.tsv --single-level \\
        --iters 240 --frame-every 1 --out frames
    ffmpeg -framerate 30 -i frames/frame_%05d.ppm -pix_fmt yuv420p dogfood.mp4

  Or the 3D HDR "big-bang" loop (scripts/glow_render3d.py). The Rust pass only
  needs to emit the static colors/radii/edges arrays — glow_render3d builds its
  own 3D layout from them — so a couple of iters is enough:

    cargo run --release -- --input dogfood_edges.tsv --single-level \\
        --iters 30 --frame-every 30 --dump-bin galaxy.bin
    # real entities are small + disjoint, so brighten the nodes with --node-gain
    python scripts/glow_render3d.py galaxy.bin frames3d --frames 360 --node-gain 3
    ffmpeg -framerate 30 -i frames3d/orbit_%05d.ppm -pix_fmt yuv420p dogfood3d.mp4

The dataset has a deliberately *skewed* cluster-size distribution (a few
heavily re-entered entities + a long tail), so node-radius-by-cluster-size makes
the big resolved entities read as big dots. Connected components of the scored
pairs ARE the resolved entities — the colored blobs are goldenmatch's output.

NB: dedupe pairs only ever link records *within* one entity, so the resolved
graph is disjoint clusters — there are no inter-entity bridges (the "cosmic web"
threads only appear on graphs that have cross-community edges). Each glowing orb
is one real resolved entity; `--node-gain` compensates for real entities being
much smaller (a few records each) than the dense synthetic showcase clusters.
"""
from __future__ import annotations

import csv
import random
import sys

import polars as pl

from goldenmatch import dedupe_df

FIRST = ["James", "John", "Robert", "Mary", "Patricia", "Jennifer", "Michael", "Linda",
         "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Sarah",
         "Thomas", "Karen", "Charles", "Nancy"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Taylor", "Moore", "Jackson", "Martin", "Lee"]
CITY = ["Springfield", "Riverside", "Franklin", "Greenville", "Bristol", "Clinton",
        "Fairview", "Salem", "Madison", "Georgetown"]


def _typo(s: str) -> str:
    """Swap two adjacent characters — a cheap stand-in for OCR/transcription noise."""
    if len(s) < 3:
        return s
    i = random.randrange(len(s) - 1)
    chars = list(s)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def build_dataset(entities: int = 140, seed: int = 5) -> pl.DataFrame:
    """`entities` true people, each re-entered 1..32 times with name/city noise.
    Skewed: ~8% are heavily duplicated (14-32 records), the rest a short tail."""
    random.seed(seed)
    rows = []
    for _ in range(entities):
        fn, ln = random.choice(FIRST), random.choice(LAST)
        city, zc = random.choice(CITY), f"{random.randint(10000, 99999)}"
        r = random.random()
        n = (random.randint(14, 32) if r < 0.08
             else random.randint(5, 10) if r < 0.25
             else random.randint(1, 3))
        for v in range(n):
            f, l, c = fn, ln, city
            if v > 0:  # the first record is the clean canonical; the rest are noisy
                if random.random() < 0.5:
                    f = _typo(f)
                if random.random() < 0.4:
                    l = _typo(l)
                if random.random() < 0.3:
                    f = f.upper()
                if random.random() < 0.2:
                    c = _typo(c)
            rows.append({"first_name": f, "last_name": l, "city": c, "zip": zc})
    return pl.DataFrame(rows)


def main() -> None:
    # ~600 true entities -> a populous galaxy of resolved entities (override with
    # the first CLI arg, e.g. `python examples/dogfood_goldenmatch.py 140`).
    entities = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    df = build_dataset(entities=entities)
    print(f"dataset: {df.height} records")

    # The real engine. Explicit fuzzy/blocking kwargs keep this offline + fast
    # (no auto-config rerank model download); zip blocks the noisy variants.
    res = dedupe_df(
        df,
        fuzzy={"first_name": 0.82, "last_name": 0.82},
        blocking=["zip"],
        threshold=0.82,
        confidence_required=False,
    )
    pairs = res.scored_pairs or []
    sizes = sorted((c.get("size", len(c.get("members", []))) for c in res.clusters.values()),
                   reverse=True)
    print(f"engine: {len(pairs)} scored pairs, {len(res.clusters)} clusters "
          f"(largest: {sizes[:6]})")

    out = "dogfood_pairs.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["a", "b", "score"])
        for a, b, s in pairs:
            w.writerow([a, b, f"{s:.4f}"])
    print(f"wrote {out}  ->  see this file's docstring for the export/layout/ffmpeg steps")


if __name__ == "__main__":
    main()
