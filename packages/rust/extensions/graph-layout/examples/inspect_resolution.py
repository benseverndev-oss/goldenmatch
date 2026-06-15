#!/usr/bin/env python3
"""Resolution inspector: an auditable view of a goldenmatch dedupe run.

Not a trailer -- a QA tool. The engine already computes the audit signals
(`oversized`, `confidence`, `cluster_quality`, `bottleneck_pair`, `pair_scores`);
this surfaces them so a developer can SEE why records merged and find the merges
most likely to be wrong. Output is one self-contained HTML file (canvas + vanilla
JS, no deps, no server) you open in a browser:

  - every record is a dot, grouped into the entity it resolved to (an orb)
  - orb size = how many records collapsed into it (big = heavily duplicated)
  - edges = the scored pairs that hold an entity together, coloured by score
    (red = barely over threshold = fragile, green = strong)
  - the left rail lists entities FRAGILE-FIRST (weakest bottleneck on top) plus
    any the engine flagged `oversized` -- start at the top, audit down
  - click an orb -> the evidence: its records + every pair score, weakest first,
    with the bottleneck pair (the single merge the whole entity hangs on) marked

  python examples/inspect_resolution.py            # -> resolution_inspector.html
  python examples/inspect_resolution.py -o out.html --entities 120 --threshold 0.82

The colour an orb gets is its risk, not decoration: grey ok, amber fragile
(bottleneck within --margin of threshold), red oversized.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

import random  # noqa: E402

import polars as pl  # noqa: E402

from goldenmatch import dedupe_df  # noqa: E402

FIRST = ["James", "John", "Robert", "Mary", "Patricia", "Michael", "Linda", "David",
         "Barbara", "Richard", "Susan", "Joseph", "Thomas", "Karen", "Charles", "Nancy"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Wilson", "Anderson", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Clark"]
CITY = ["Springfield", "Riverside", "Franklin", "Greenville", "Bristol", "Salem"]


def _corrupt(s, rng, heavy):
    """Layered noise: transpositions, deletions, case, abbreviation. `heavy`
    pushes some intra-entity pairs down near the match threshold so the engine
    produces genuinely *fragile* merges (a chain held by one weak link)."""
    if rng.random() < 0.45:
        i = rng.randrange(max(len(s) - 1, 1)); c = list(s)
        if i + 1 < len(c):
            c[i], c[i + 1] = c[i + 1], c[i]; s = "".join(c)
    if heavy and rng.random() < 0.5 and len(s) > 4:
        i = rng.randrange(len(s)); s = s[:i] + s[i + 1:]          # drop a char
    if rng.random() < 0.3:
        s = s.upper()
    if heavy and rng.random() < 0.3 and len(s) > 3:
        s = s[0] + "."                                            # initial only
    return s


def build_audit_dataset(entities: int, seed: int) -> pl.DataFrame:
    """Messy people data engineered to exercise the auditor: heavy intra-entity
    noise (=> fragile chains) plus deliberate cross-entity collisions (two
    DIFFERENT people sharing a zip with a near-identical name => the engine may
    over-merge them). The flags the inspector shows are the REAL engine's calls
    on this hard input -- nothing is hand-labelled."""
    rng = random.Random(seed)
    rows, zips_used = [], []
    for _ in range(entities):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        collide = zips_used and rng.random() < 0.16          # reuse a hot zip
        zc = rng.choice(zips_used) if collide else f"{rng.randint(10000, 99999)}"
        if not collide:
            zips_used.append(zc)
        r = rng.random()
        n = (rng.randint(9, 16) if r < 0.08 else
             rng.randint(4, 8) if r < 0.30 else rng.randint(1, 3))
        heavy = rng.random() < 0.5
        for v in range(n):
            f, l, c = fn, ln, rng.choice(CITY)
            if v > 0:
                f = _corrupt(f, rng, heavy)
                if rng.random() < 0.5:
                    l = _corrupt(l, rng, heavy)
            rows.append({"first_name": f, "last_name": l, "city": c, "zip": zc})
    return pl.DataFrame(rows)


def layout(clusters, n_records, seed=7):
    """2D positions: cluster centroids by repulsion (dedupe clusters are
    disjoint, so there are no attractive inter-cluster edges), members fanned
    around their centroid by phyllotaxis. Returns (N,2) per-record array."""
    rng = np.random.default_rng(seed)
    cids = list(clusters)
    C = rng.standard_normal((len(cids), 2))
    sizes = np.array([clusters[c]["size"] for c in cids], float)
    for _ in range(420):
        d = C[:, None, :] - C[None, :, :]
        dist2 = (d * d).sum(-1) + 1e-3
        rep = (d / dist2[:, :, None] ** 1.2).sum(1) * 0.05
        C += rep - C * 0.010
    C -= C.mean(0)
    C /= np.abs(C).max() + 1e-6
    C *= 900.0                                   # world units

    pos = np.zeros((n_records, 2))
    ga = math.pi * (3 - math.sqrt(5))            # golden angle
    for ci, cid in enumerate(cids):
        members = clusters[cid]["members"]
        m = len(members)
        rad = 6.0 + 11.0 * math.sqrt(max(m - 1, 0))
        for k, rid in enumerate(members):
            if m == 1:
                pos[rid] = C[ci]
            else:
                r = rad * math.sqrt((k + 0.5) / m)
                a = k * ga
                pos[rid] = C[ci] + (r * math.cos(a), r * math.sin(a))
    return pos, {cid: C[i] for i, cid in enumerate(cids)}


def build_payload(df, res, threshold, margin):
    fields = df.columns
    rows = df.to_dicts()
    clusters = {int(c): v for c, v in res.clusters.items()}
    # ensure every record lands in a cluster (singletons may be implicit)
    seen = {rid for c in clusters.values() for rid in c["members"]}
    nxt = (max(clusters) + 1) if clusters else 0
    for rid in range(df.height):
        if rid not in seen:
            clusters[nxt] = {"members": [rid], "size": 1, "confidence": 1.0,
                             "cluster_quality": "singleton", "oversized": False,
                             "bottleneck_pair": None, "pair_scores": {}}
            nxt += 1

    pos, centroids = layout(clusters, df.height)

    nodes = []
    rid_to_cluster = {}
    for cid, c in clusters.items():
        for rid in c["members"]:
            rid_to_cluster[rid] = cid
    for rid in range(df.height):
        nodes.append({"id": rid, "x": round(float(pos[rid][0]), 1),
                      "y": round(float(pos[rid][1]), 1),
                      "c": rid_to_cluster.get(rid, -1),
                      "f": {k: rows[rid][k] for k in fields}})

    out_clusters, edges = [], []
    for cid, c in clusters.items():
        ps = c.get("pair_scores") or {}
        # pair_scores keys may be tuples (py) -> normalise to [a,b,score]
        plist = []
        for k, sc in (ps.items() if isinstance(ps, dict) else []):
            a, b = k
            plist.append([int(a), int(b), round(float(sc), 4)])
            edges.append([int(a), int(b), round(float(sc), 4)])
        bn = c.get("bottleneck_pair")
        bn = [int(bn[0]), int(bn[1])] if bn else None
        bn_score = None
        if bn is not None:
            for a, b, sc in plist:
                if {a, b} == set(bn):
                    bn_score = sc; break
        size = c["size"]
        fragile = (size >= 2 and bn_score is not None and bn_score < threshold + margin)
        status = "oversized" if c.get("oversized") else ("fragile" if fragile else "ok")
        cx, cy = centroids[cid]
        out_clusters.append({
            "id": cid, "size": size, "members": c["members"],
            "confidence": round(float(c.get("confidence", 1.0)), 4),
            "quality": c.get("cluster_quality", ""),
            "oversized": bool(c.get("oversized")),
            "bottleneck": bn, "bottleneck_score": bn_score,
            "status": status, "pairs": sorted(plist, key=lambda p: p[2]),
            "x": round(float(cx), 1), "y": round(float(cy), 1),
        })

    sizes = [c["size"] for c in out_clusters]
    summary = {
        "records": df.height, "entities": len(out_clusters),
        "fields": fields, "threshold": threshold, "margin": margin,
        "multi": sum(1 for s in sizes if s >= 2),
        "singletons": sum(1 for s in sizes if s == 1),
        "oversized": sum(1 for c in out_clusters if c["oversized"]),
        "fragile": sum(1 for c in out_clusters if c["status"] == "fragile"),
        "max_size": max(sizes) if sizes else 0,
    }
    return {"summary": summary, "nodes": nodes, "clusters": out_clusters, "edges": edges}


HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>goldenmatch resolution inspector</title>
<style>
 :root{--bg:#0d1016;--panel:#161b25;--line:#283042;--ink:#dde3ee;--dim:#8a94a6;
   --ok:#5a6b86;--amber:#e0a23a;--red:#e0533a;--green:#46c46a;}
 *{box-sizing:border-box} html,body{margin:0;height:100%;background:#0d1016;
   color:#dde3ee;font:13px/1.45 ui-monospace,Menlo,Consolas,monospace}
 #wrap{display:flex;height:100vh}
 #rail{width:300px;flex:none;background:#12161f;border-right:1px solid #283042;
   display:flex;flex-direction:column}
 #sum{padding:12px 14px;border-bottom:1px solid #283042}
 #sum h1{font-size:13px;margin:0 0 8px;letter-spacing:.06em;color:#aeb8cc}
 #sum .g{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px}
 #sum b{color:#fff} .pill{padding:1px 6px;border-radius:8px;font-size:11px}
 .pill.red{background:#3a1d18;color:#f0907a} .pill.amber{background:#3a2f18;color:#e7c074}
 #listhdr{padding:8px 14px;color:#8a94a6;border-bottom:1px solid #283042;font-size:11px}
 #list{overflow:auto;flex:1}
 .row{padding:7px 14px;border-bottom:1px solid #1b212d;cursor:pointer;display:flex;
   gap:8px;align-items:center} .row:hover{background:#1b2230}
 .dot{width:9px;height:9px;border-radius:50%;flex:none}
 .dot.ok{background:#5a6b86}.dot.fragile{background:#e0a23a}.dot.oversized{background:#e0533a}
 .row .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .row .sc{color:#8a94a6;font-size:11px}
 #main{flex:1;position:relative} canvas{display:block;width:100%;height:100%}
 #tip{position:absolute;pointer-events:none;background:#0a0d13ee;border:1px solid #283042;
   padding:6px 8px;border-radius:5px;font-size:12px;max-width:280px;display:none;z-index:5}
 #panel{position:absolute;top:0;right:0;width:360px;max-height:100%;overflow:auto;
   background:#161b25f5;border-left:1px solid #283042;padding:14px;display:none}
 #panel h2{font-size:13px;margin:0 0 4px} #panel .sub{color:#8a94a6;margin-bottom:10px}
 #panel x{position:absolute;top:10px;right:12px;cursor:pointer;color:#8a94a6}
 table{width:100%;border-collapse:collapse;margin:6px 0 14px} th,td{text-align:left;
   padding:3px 6px;border-bottom:1px solid #222a38;font-size:12px;vertical-align:top}
 th{color:#8a94a6;font-weight:400} .ev{font-variant-numeric:tabular-nums}
 .bn{background:#3a2f18} .weak{color:#e7a06a} .strong{color:#6cc98a}
 #hint{position:absolute;left:12px;bottom:10px;color:#5b6577;font-size:11px}
 .tag{padding:1px 6px;border-radius:8px;font-size:11px;margin-left:6px}
 .tag.oversized{background:#3a1d18;color:#f0907a}.tag.fragile{background:#3a2f18;color:#e7c074}
 .tag.ok{background:#1f2735;color:#8fa0bd}
</style></head><body><div id="wrap">
 <div id="rail">
  <div id="sum"></div>
  <div id="listhdr">ENTITIES &mdash; fragile first &darr;</div>
  <div id="list"></div>
 </div>
 <div id="main">
  <canvas id="cv"></canvas>
  <div id="tip"></div>
  <div id="panel"></div>
  <div id="hint">scroll = zoom &middot; drag = pan &middot; click an orb for its evidence</div>
 </div></div>
<script>
const DATA = __DATA__;
const nodes=DATA.nodes, clusters=DATA.clusters, S=DATA.summary;
const byId={}; nodes.forEach(n=>byId[n.id]=n);
const cById={}; clusters.forEach(c=>cById[c.id]=c);
const COL={ok:'#5a6b86',fragile:'#e0a23a',oversized:'#e0533a'};
function hue(id){return `hsl(${(id*47)%360} 55% 60%)`;}

const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let view={x:0,y:0,k:1}, DPR=Math.max(1,devicePixelRatio||1);
function resize(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;draw();}
addEventListener('resize',resize);
function fit(){
  const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y);
  const minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);
  const w=cv.clientWidth,h=cv.clientHeight,pad=60;
  view.k=Math.min((w-2*pad)/(maxx-minx),(h-2*pad)/(maxy-miny));
  view.x=w/2-(minx+maxx)/2*view.k; view.y=h/2-(miny+maxy)/2*view.k;
}
const tx=p=>p*view.k, sx=x=>x*view.k+view.x, sy=y=>y*view.k+view.y;
let sel=null, hov=null;

function scoreColor(sc){const t=DATA.summary.threshold;
  const f=Math.max(0,Math.min(1,(sc-t)/(1-t+1e-6)));
  return `rgba(${Math.round(224-150*f)},${Math.round(83+110*f)},${Math.round(58+10*f)},0.5)`;}

function draw(){
  ctx.setTransform(DPR,0,0,DPR,0,0);
  ctx.clearRect(0,0,cv.clientWidth,cv.clientHeight);
  // cluster halos
  for(const c of clusters){ if(c.size<2 && c.status==='ok') continue;
    const r=tx(8+11*Math.sqrt(Math.max(c.size-1,0)))+7;
    ctx.beginPath();ctx.arc(sx(c.x),sy(c.y),r,0,7);
    ctx.fillStyle=(c.status==='ok')?'rgba(90,107,134,0.06)':
      (c.status==='fragile'?'rgba(224,162,58,0.10)':'rgba(224,83,58,0.13)');
    ctx.fill();
    if(c.status!=='ok'||c.id===sel){ctx.lineWidth=(c.id===sel)?2:1;
      ctx.strokeStyle=(c.id===sel)?'#fff':COL[c.status];ctx.stroke();}
  }
  // edges
  ctx.lineWidth=1;
  for(const [a,b,sc] of DATA.edges){const na=byId[a],nb=byId[b];
    ctx.strokeStyle=scoreColor(sc);ctx.beginPath();
    ctx.moveTo(sx(na.x),sy(na.y));ctx.lineTo(sx(nb.x),sy(nb.y));ctx.stroke();}
  // bottleneck edges (emphasised) for selected
  if(sel!=null){const c=cById[sel]; if(c.bottleneck){const[a,b]=c.bottleneck;
    ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();
    ctx.moveTo(sx(byId[a].x),sy(byId[a].y));ctx.lineTo(sx(byId[b].x),sy(byId[b].y));ctx.stroke();}}
  // nodes
  for(const n of nodes){const c=cById[n.c];
    const rr=Math.max(1.6,tx(3.0));
    ctx.beginPath();ctx.arc(sx(n.x),sy(n.y),rr,0,7);
    ctx.fillStyle=hue(n.c);ctx.globalAlpha=(sel==null||n.c===sel)?1:0.32;ctx.fill();ctx.globalAlpha=1;}
  if(hov){ctx.beginPath();ctx.arc(sx(hov.x),sy(hov.y),Math.max(3,tx(3.0))+2,0,7);
    ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();}
}

function pick(mx,my){let best=null,bd=12;
  for(const n of nodes){const dx=sx(n.x)-mx,dy=sy(n.y)-my,d=Math.hypot(dx,dy);
    if(d<bd){bd=d;best=n;}} return best;}

cv.addEventListener('mousemove',e=>{const r=cv.getBoundingClientRect();
  const mx=e.clientX-r.left,my=e.clientY-r.top;
  if(drag){view.x+=e.clientX-drag.x;view.y+=e.clientY-drag.y;drag={x:e.clientX,y:e.clientY};draw();return;}
  const n=pick(mx,my); if(n!==hov){hov=n;draw();}
  const tip=document.getElementById('tip');
  if(n){const c=cById[n.c];let h=Object.entries(n.f).map(([k,v])=>`<b>${k}</b> ${v}`).join('<br>');
    tip.innerHTML=h+`<hr style="border:0;border-top:1px solid #283042;margin:5px 0">`+
      `entity #${n.c} &middot; ${c.size} rec &middot; <span class="tag ${c.status}">${c.status}</span>`;
    tip.style.display='block';tip.style.left=(mx+14)+'px';tip.style.top=(my+12)+'px';}
  else tip.style.display='none';});
let drag=null;
cv.addEventListener('mousedown',e=>{drag={x:e.clientX,y:e.clientY};});
addEventListener('mouseup',()=>drag=null);
cv.addEventListener('click',e=>{const r=cv.getBoundingClientRect();
  const n=pick(e.clientX-r.left,e.clientY-r.top);
  if(n){select(n.c);} else {sel=null;document.getElementById('panel').style.display='none';draw();}});
cv.addEventListener('wheel',e=>{e.preventDefault();const r=cv.getBoundingClientRect();
  const mx=e.clientX-r.left,my=e.clientY-r.top;const f=Math.exp(-e.deltaY*0.0012);
  view.x=mx-(mx-view.x)*f;view.y=my-(my-view.y)*f;view.k*=f;draw();},{passive:false});

function select(cid){sel=cid;draw();
  const c=cById[cid];const p=document.getElementById('panel');
  const recRows=c.members.map(id=>{const f=byId[id].f;
    return `<tr><td>${id}</td>`+DATA.summary.fields.map(k=>`<td>${f[k]}</td>`).join('')+`</tr>`;}).join('');
  const ev=c.pairs.map(([a,b,sc])=>{const isbn=c.bottleneck&&((a==c.bottleneck[0]&&b==c.bottleneck[1])||(a==c.bottleneck[1]&&b==c.bottleneck[0]));
    const cls=sc<DATA.summary.threshold+DATA.summary.margin?'weak':'strong';
    return `<tr class="${isbn?'bn':''}"><td>${a}&ndash;${b}</td><td class="ev ${cls}">${sc.toFixed(3)}</td><td>${isbn?'&larr; bottleneck':''}</td></tr>`;}).join('');
  p.innerHTML=`<x onclick="this.parentNode.style.display='none';sel=null;draw()">&times;</x>`+
   `<h2>entity #${cid} <span class="tag ${c.status}">${c.status}</span></h2>`+
   `<div class="sub">${c.size} records &middot; confidence ${c.confidence} &middot; ${c.quality}`+
   (c.bottleneck_score!=null?` &middot; bottleneck ${c.bottleneck_score.toFixed(3)}`:'')+`</div>`+
   `<table><tr><th>id</th>${DATA.summary.fields.map(k=>`<th>${k}</th>`).join('')}</tr>${recRows}</table>`+
   (c.pairs.length?`<div style="color:#8a94a6;margin-bottom:4px">evidence &mdash; pair scores, weakest first</div>`+
     `<table><tr><th>pair</th><th>score</th><th></th></tr>${ev}</table>`:`<div style="color:#8a94a6">singleton &mdash; no merge evidence</div>`);
  p.style.display='block';
}

// summary + rail
const sm=document.getElementById('sum');
sm.innerHTML=`<h1>RESOLUTION INSPECTOR</h1><div class="g">`+
 `<span>records</span><b>${S.records}</b>`+
 `<span>entities</span><b>${S.entities}</b>`+
 `<span>multi-record</span><b>${S.multi}</b>`+
 `<span>singletons</span><b>${S.singletons}</b>`+
 `<span>oversized</span><b>${S.oversized?`<span class="pill red">${S.oversized}</span>`:0}</b>`+
 `<span>fragile</span><b>${S.fragile?`<span class="pill amber">${S.fragile}</span>`:0}</b>`+
 `<span>largest</span><b>${S.max_size}</b>`+
 `<span>threshold</span><b>${S.threshold}</b></div>`;
const order={oversized:0,fragile:1,ok:2};
const ranked=clusters.filter(c=>c.size>=2).slice().sort((a,b)=>
  (order[a.status]-order[b.status]) ||
  ((a.bottleneck_score??1)-(b.bottleneck_score??1)) || (b.size-a.size));
const list=document.getElementById('list');
list.innerHTML=ranked.map(c=>{const f=byId[c.members[0]].f;
  const nm=DATA.summary.fields.slice(0,2).map(k=>f[k]).join(' ');
  return `<div class="row" data-c="${c.id}"><span class="dot ${c.status}"></span>`+
   `<span class="nm">#${c.id} ${nm}</span>`+
   `<span class="sc">${c.size}rec${c.bottleneck_score!=null?' &middot; '+c.bottleneck_score.toFixed(2):''}</span></div>`;}).join('');
list.querySelectorAll('.row').forEach(r=>r.onclick=()=>{const c=cById[+r.dataset.c];
  view.k=2.4;view.x=cv.clientWidth/2-c.x*view.k;view.y=cv.clientHeight/2-c.y*view.k;select(c.id);});

resize();fit();draw();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="resolution_inspector.html")
    ap.add_argument("--entities", type=int, default=120)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=0.82)
    ap.add_argument("--margin", type=float, default=0.04,
                    help="bottleneck within this of threshold => flagged fragile")
    args = ap.parse_args()

    df = build_audit_dataset(entities=args.entities, seed=args.seed)
    res = dedupe_df(df, fuzzy={"first_name": 0.80, "last_name": 0.80},
                    blocking=["zip"], threshold=args.threshold,
                    confidence_required=False)
    payload = build_payload(df, res, args.threshold, args.margin)
    s = payload["summary"]
    print(f"{s['records']} records -> {s['entities']} entities "
          f"({s['multi']} multi, {s['singletons']} singletons); "
          f"{s['oversized']} oversized, {s['fragile']} fragile")
    html = HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(html)//1024} KB) -- open in a browser")


if __name__ == "__main__":
    main()
