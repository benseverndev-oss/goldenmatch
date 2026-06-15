#!/usr/bin/env python3
"""Dogfood the REAL graph/relationship ER engine -> a galaxy WITH a cosmic web.

Plain dedupe produces disjoint clusters (no inter-entity edges), so the glow
loop has no bridges. Multi-entity *graph* ER does: two entity types (customers +
orders) resolved independently, then linked by a relationship
(`orders.customer_id -> customers.customer_id`). Those relationship links are
edges *between different resolved entities* -> the bridges that form the web.

Pipeline (all real engine, no hand-built graph):

    customers.csv + orders.csv  (noisy dupes + a customer_id relationship)
      -> goldenmatch.run_graph_er  (resolve each type, propagate evidence)
      -> nodes coloured by resolved cluster, intra edges = scored pairs,
         inter edges = relationship links
      -> galaxy_web.bin  (the glow_render3d input format, written directly so
         colour == resolved entity rather than connected component)

    python examples/dogfood_graph_er.py galaxy_web.bin
    python scripts/glow_render3d.py galaxy_web.bin frames --frames 360 --node-gain 2.2
    ffmpeg -framerate 30 -i frames/orbit_%05d.ppm -pix_fmt yuv420p web.mp4

Each orb is one resolved entity (customer or order, sized by record count); each
bridge is a real customer<->order relationship the graph-ER engine propagated
evidence across.
"""
from __future__ import annotations

import csv
import random
import struct
import sys
from pathlib import Path

import polars as pl

from goldenmatch import run_graph_er
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.graph_er import EntityType, Relationship

FIRST = ["James", "John", "Robert", "Mary", "Patricia", "Jennifer", "Michael", "Linda",
         "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Sarah",
         "Thomas", "Karen", "Charles", "Nancy", "Daniel", "Lisa", "Paul", "Betty"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Taylor", "Moore", "Jackson", "Martin", "Lee", "Clark", "Lewis", "Walker", "Hall"]
PRODUCT = ["Widget", "Gadget", "Sprocket", "Cog", "Gizmo", "Doohickey", "Thingamajig",
           "Contraption", "Apparatus", "Module", "Bracket", "Flange"]


def _typo(s: str) -> str:
    if len(s) < 3:
        return s
    i = random.randrange(len(s) - 1)
    c = list(s); c[i], c[i + 1] = c[i + 1], c[i]
    return "".join(c)


def build(customers: int, seed: int):
    """`customers` true people, each re-entered (noisy dupes); each owns a few
    orders, each order re-entered (exact dupes on order_id). Returns the two
    record lists keyed by a shared customer_id relationship."""
    random.seed(seed)
    cust_rows, order_rows = [], []
    oid = 0
    for ci in range(customers):
        cid = f"C{ci:05d}"
        fn, ln = random.choice(FIRST), random.choice(LAST)
        zc = f"{random.randint(10000, 99999)}"
        r = random.random()
        ndup = (random.randint(8, 14) if r < 0.07 else
                random.randint(4, 7) if r < 0.25 else random.randint(1, 3))
        for v in range(ndup):
            f, l = fn, ln
            if v > 0:
                if random.random() < 0.5: f = _typo(f)
                if random.random() < 0.4: l = _typo(l)
                if random.random() < 0.3: f = f.upper()
            cust_rows.append({"customer_id": cid, "name": f"{f} {l}", "zip": zc})
        for _ in range(random.randint(1, 3)):           # this customer's orders
            ostr = f"O{oid:06d}"; oid += 1
            prod = random.choice(PRODUCT); amt = random.choice([50, 100, 150, 200, 250, 500])
            for _v in range(random.randint(1, 2)):      # exact order dupes
                order_rows.append({"order_id": ostr, "customer_id": cid,
                                   "product": prod, "amount": str(amt)})
    return cust_rows, order_rows


def _write_csv(path: Path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


def _cluster_map(entity):
    """row_id -> (cluster_key, size) for every record (singletons get own key)."""
    out, sizes = {}, {}
    for cid, c in entity.clusters.items():
        members = c.get("members", [])
        key = (entity.name, "c", cid)
        sizes[key] = c.get("size", len(members))
        for rid in members:
            out[rid] = key
    return out, sizes


def export_bin(result, relationships, out_path: str, res: int = 1080):
    """Write the glow_render3d .bin: colour == resolved cluster, intra edges ==
    scored pairs, inter edges == relationship links (the bridges)."""
    node_index, node_color, node_size = {}, [], []
    color_of, next_color = {}, 0

    def color_id(key):
        nonlocal next_color
        if key not in color_of:
            color_of[key] = next_color; next_color += 1
        return color_of[key]

    edges = []
    for ent in result.entities.values():
        df = ent.df
        rid_col = df["__row_id__"].to_list()
        cmap, sizes = _cluster_map(ent)
        singleton = 0
        for pos, rid in enumerate(rid_col):
            if rid in cmap:
                key = cmap[rid]; sz = sizes[key]
            else:
                key = (ent.name, "s", rid); sz = 1; singleton += 1
            gid = len(node_color)
            node_index[(ent.name, rid)] = gid
            node_color.append(color_id(key)); node_size.append(float(sz))
        for a, b, _s in ent.scored_pairs:                # intra (same colour)
            ga, gb = node_index.get((ent.name, a)), node_index.get((ent.name, b))
            if ga is not None and gb is not None and ga != gb:
                edges.append((ga, gb))

    # inter edges: relationship join_key links across entity types == the web
    ents = result.entities
    for rel in relationships:
        fe, te = ents.get(rel.from_entity), ents.get(rel.to_entity)
        if fe is None or te is None:
            continue
        key = rel.join_key
        # value -> target node ids (cap fan-out so bridges read as threads)
        tgt = {}
        for pos, rid in enumerate(te.df["__row_id__"].to_list()):
            tgt.setdefault(te.df[key][pos], []).append(node_index[(te.name, rid)])
        for pos, rid in enumerate(fe.df["__row_id__"].to_list()):
            gfrom = node_index[(fe.name, rid)]
            for gto in tgt.get(fe.df[key][pos], [])[:2]:
                if gfrom != gto:
                    edges.append((gfrom, gto))

    # de-dup undirected edges
    seen, uniq = set(), []
    for a, b in edges:
        k = (a, b) if a < b else (b, a)
        if k not in seen:
            seen.add(k); uniq.append(k)

    n, m = len(node_color), len(uniq)
    inter = sum(1 for a, b in uniq if node_color[a] != node_color[b])
    with open(out_path, "wb") as f:
        f.write(struct.pack("<IIII", n, m, res, res))
        f.write(struct.pack(f"<{n}I", *node_color))
        f.write(struct.pack(f"<{n}f", *node_size))
        flat = [v for e in uniq for v in e]
        f.write(struct.pack(f"<{2 * m}I", *flat))
    print(f"wrote {out_path}: {n} nodes, {m} edges ({inter} bridges), "
          f"{next_color} resolved entities")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "galaxy_web.bin"
    customers = int(sys.argv[2]) if len(sys.argv) > 2 else 240
    cust_rows, order_rows = build(customers, seed=5)
    tmp = Path("/tmp/_graph_er_dogfood"); tmp.mkdir(exist_ok=True)
    cust_path = _write_csv(tmp / "customers.csv", ["customer_id", "name", "zip"], cust_rows)
    order_path = _write_csv(tmp / "orders.csv",
                            ["order_id", "customer_id", "product", "amount"], order_rows)
    print(f"dataset: {len(cust_rows)} customer records, {len(order_rows)} order records")

    entities = [
        EntityType(name="customers", sources=[(cust_path, "cust")], config=GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(name="cust_fuzzy", type="weighted", threshold=0.7, fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0),
                MatchkeyField(field="zip", scorer="exact", weight=0.5)])],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
            golden_rules=GoldenRulesConfig(default_strategy="most_complete"))),
        EntityType(name="orders", sources=[(order_path, "orders")], config=GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(name="order_exact", type="exact",
                                      fields=[MatchkeyField(field="order_id")])])),
    ]
    relationships = [Relationship(from_entity="orders", to_entity="customers",
                                  join_key="customer_id", evidence_weight=0.2)]
    result = run_graph_er(entities, relationships, max_iterations=4)
    print(f"graph ER: {result.iterations} iters, converged={result.converged}, "
          f"evidence_propagated={result.evidence_propagated}")
    export_bin(result, relationships, out)


if __name__ == "__main__":
    main()
