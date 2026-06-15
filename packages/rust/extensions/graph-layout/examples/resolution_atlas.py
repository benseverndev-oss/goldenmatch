#!/usr/bin/env python3
"""Resolution Atlas: a live, glowing map of a goldenmatch dedupe run.

One self-contained HTML file (canvas + vanilla JS, no deps, no server) that is
meant to be three things at once -- a piece that looks good, and a tool you'd
actually use:

  * a force-directed map of every record, glowing, grouped into the entity it
    resolved to (orb size = how many records collapsed in);
  * a LIVE THRESHOLD SLIDER -- drag it and the entities merge and split in real
    time as the match cutoff moves. This is the core entity-resolution decision,
    made visible: too low and distinct people fuse, too high and one person
    shatters into fragments;
  * risk as light -- an entity held together by a single near-threshold link
    (a "fragile" merge, the kind most likely wrong) glows hot and pulses, so the
    thing you should audit is the thing your eye is drawn to. Click it for the
    evidence: its records and every pair score, weakest first, bottleneck marked.

The clustering is recomputed client-side (union-find over the real scored pairs)
so the slider is instant; the flags are the engine's own numbers on deliberately
messy input -- nothing hand-labelled.

  python examples/resolution_atlas.py                 # -> resolution_atlas.html
  python examples/resolution_atlas.py --png shot.png  # also a static preview
  python examples/resolution_atlas.py --entities 140 --threshold 0.82
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from inspect_resolution import build_audit_dataset  # noqa: E402

from goldenmatch import dedupe_df  # noqa: E402

FLOOR = 0.60            # capture pairs down to here so the slider has range


def union_find(n, pairs, thr):
    par = list(range(n))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for a, b, s in pairs:
        if s >= thr:
            ra, rb = find(a), find(b)
            if ra != rb:
                par[max(ra, rb)] = min(ra, rb)
    return [find(i) for i in range(n)]


def seed_layout(n, pairs, thr, seed=7):
    """Clean two-level layout: components (at the default threshold) become
    distinct orbs via a centroid force sim; members are fanned around their
    centroid by phyllotaxis. Disjoint, readable -- no hairball."""
    comp = union_find(n, pairs, thr)
    members = {}
    for i, c in enumerate(comp):
        members.setdefault(c, []).append(i)
    cids = list(members)
    rng = np.random.default_rng(seed)
    C = rng.standard_normal((len(cids), 2))
    for _ in range(420):
        d = C[:, None, :] - C[None, :, :]
        dist2 = (d * d).sum(-1) + 1e-3
        rep = (d / dist2[:, :, None] ** 1.2).sum(1) * 0.05
        C += rep - C * 0.010
    C -= C.mean(0)
    C /= np.abs(C).max() + 1e-6
    C *= 1000.0
    pos = np.zeros((n, 2))
    ga = math.pi * (3 - math.sqrt(5))
    for ci, cid in enumerate(cids):
        m = members[cid]
        rad = 7.0 + 12.0 * math.sqrt(max(len(m) - 1, 0))
        for k, rid in enumerate(m):
            if len(m) == 1:
                pos[rid] = C[ci]
            else:
                r = rad * math.sqrt((k + 0.5) / len(m))
                a = k * ga
                pos[rid] = C[ci] + (r * math.cos(a), r * math.sin(a))
    return pos


def palette(cid):
    h = ((cid * 0.61803398875) % 1.0)
    r, g, b = colorsys.hls_to_rgb(h, 0.62, 0.55)
    return (int(r * 255), int(g * 255), int(b * 255))


def build_payload(df, pairs, thr):
    fields = df.columns
    rows = df.to_dicts()
    n = df.height
    pos = seed_layout(n, pairs, thr)
    records = [[rid] + [rows[rid][k] for k in fields] for rid in range(n)]
    P = [[int(a), int(b), round(float(s), 3)] for a, b, s in pairs]
    return {
        "fields": fields,
        "records": records,
        "pairs": P,
        "pos": [[round(float(x), 1), round(float(y), 1)] for x, y in pos],
        "threshold": thr, "floor": FLOOR, "n": n,
    }


# ---- optional static PNG preview (faithful: same positions + palette + glow) --
def render_png(payload, thr, out, W=1600, H=1000):
    from PIL import Image, ImageDraw, ImageFilter
    n = payload["n"]; pos = payload["pos"]; pairs = payload["pairs"]
    comp = union_find(n, [(a, b, s) for a, b, s in pairs], thr)
    members = {}
    for i, c in enumerate(comp):
        members.setdefault(c, []).append(i)
    # bottleneck per component (min active internal edge)
    minedge = {}
    for a, b, s in pairs:
        if s >= thr and comp[a] == comp[b]:
            c = comp[a]; minedge[c] = min(minedge.get(c, 9), s)
    xs = [p[0] for p in pos]; ys = [p[1] for p in pos]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    k = min((W - 160) / (maxx - minx + 1), (H - 160) / (maxy - miny + 1))
    def SX(x): return (x - (minx + maxx) / 2) * k + W / 2
    def SY(y): return (y - (miny + maxy) / 2) * k + H / 2

    base = Image.new("RGB", (W, H), (7, 10, 16))
    # vignette gradient
    vg = Image.new("L", (W, H), 0); vd = ImageDraw.Draw(vg)
    vd.ellipse([-W * 0.2, -H * 0.2, W * 1.2, H * 1.2], fill=40)
    base = Image.composite(Image.new("RGB", (W, H), (12, 16, 26)), base, vg.filter(ImageFilter.GaussianBlur(120)))
    glow = Image.new("RGB", (W, H), (0, 0, 0)); gd = ImageDraw.Draw(glow)
    line = Image.new("RGB", (W, H), (0, 0, 0)); ld = ImageDraw.Draw(line, "RGBA")

    for a, b, s in pairs:
        if s < thr or comp[a] != comp[b]:
            continue
        f = max(0, min(1, (s - thr) / (1 - thr + 1e-6)))
        col = (int(235 - 150 * f), int(150 + 80 * f), int(120 + 80 * f), int(60 + 90 * f))
        ld.line([SX(pos[a][0]), SY(pos[a][1]), SX(pos[b][0]), SY(pos[b][1])], fill=col, width=1)

    def node_rgb(i):
        c = comp[i]; r, g, b = palette(c)
        me = minedge.get(c, 1.0)
        if len(members[c]) >= 2 and me < thr + 0.04:           # fragile -> warm
            return (240, 150, 70)
        return (r, g, b)
    for i in range(n):
        x, y = SX(pos[i][0]), SY(pos[i][1])
        rr = 5 + 2.4 * math.sqrt(max(len(members[comp[i]]) - 1, 0))   # size = dup count
        gd.ellipse([x - rr, y - rr, x + rr, y + rr], fill=node_rgb(i))
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    out_img = Image.fromarray(np.clip(
        np.asarray(base, float) + np.asarray(line, float) +
        np.asarray(glow, float) * 1.5, 0, 255).astype("uint8"))
    # crisp cores on top
    cd = ImageDraw.Draw(out_img)
    for i in range(n):
        x, y = SX(pos[i][0]), SY(pos[i][1]); r, g, b = node_rgb(i)
        cd.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=(min(r + 40, 255), min(g + 40, 255), min(b + 40, 255)))
    out_img.save(out)
    ent = len(members)
    frag = sum(1 for c, m in members.items() if len(m) >= 2 and minedge.get(c, 1) < thr + 0.04)
    print(f"png {out}: {ent} entities, {frag} fragile @ threshold {thr}")


HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>goldenmatch &middot; resolution atlas</title>
<style>
 *{box-sizing:border-box;margin:0;padding:0}
 html,body{height:100%;background:#05070b;color:#e7ecf5;overflow:hidden;
   font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
 #cv{position:fixed;inset:0}
 .glass{background:rgba(14,18,28,.72);backdrop-filter:blur(14px);
   border:1px solid rgba(255,255,255,.07);border-radius:14px}
 #top{position:fixed;top:18px;left:18px;right:18px;display:flex;align-items:center;
   gap:22px;padding:12px 18px;z-index:3}
 #top h1{font-size:13px;font-weight:600;letter-spacing:.14em;color:#aab6cc;
   text-transform:uppercase;white-space:nowrap}
 #top h1 b{color:#fff}
 .stat{display:flex;flex-direction:column;line-height:1.1}
 .stat .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
 .stat .l{font-size:10px;letter-spacing:.12em;color:#7f8aa0;text-transform:uppercase;margin-top:2px}
 .stat.warm .v{color:#ffb15a} .spacer{flex:1}
 #slug{display:flex;align-items:center;gap:14px;min-width:340px}
 #slug .t{font-size:11px;letter-spacing:.1em;color:#8a94a6;text-transform:uppercase}
 input[type=range]{-webkit-appearance:none;appearance:none;height:4px;border-radius:3px;
   background:linear-gradient(90deg,#2b3344,#5b6b86);outline:none;width:230px}
 input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;
   border-radius:50%;background:#fff;cursor:pointer;box-shadow:0 0 10px rgba(255,255,255,.55)}
 #thv{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;min-width:46px;text-align:right}
 #panel{position:fixed;top:78px;right:18px;width:340px;max-height:calc(100% - 110px);
   overflow:auto;padding:16px 18px;z-index:3;display:none}
 #panel h2{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}
 #panel .sub{color:#8a94a6;font-size:12px;margin:5px 0 12px}
 .badge{font-size:10px;letter-spacing:.08em;padding:2px 8px;border-radius:20px;text-transform:uppercase}
 .badge.ok{background:rgba(120,150,200,.16);color:#a9bbdb}
 .badge.fragile{background:rgba(255,177,90,.16);color:#ffc27a}
 .badge.oversized{background:rgba(255,90,70,.18);color:#ff9a85}
 table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:4px 6px;
   border-bottom:1px solid rgba(255,255,255,.06);font-size:12px}
 th{color:#7f8aa0;font-weight:500;font-size:10px;letter-spacing:.08em;text-transform:uppercase}
 .mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
 tr.bn{background:rgba(255,177,90,.10)} .weak{color:#ffb15a} .strong{color:#7fd6a0}
 #panel x{margin-left:auto;cursor:pointer;color:#8a94a6;font-size:18px;line-height:1}
 #tip{position:fixed;pointer-events:none;z-index:4;padding:8px 11px;font-size:12px;
   display:none;max-width:260px} #tip .k{color:#8a94a6}
 #hint{position:fixed;left:20px;bottom:16px;color:#586074;font-size:11px;z-index:3}
 #frag{cursor:pointer;user-select:none} #frag:hover .v{text-decoration:underline}
</style></head><body>
<canvas id="cv"></canvas>
<div id="top" class="glass">
  <h1>resolution <b>atlas</b></h1>
  <div class="stat"><span class="v" id="s_rec">0</span><span class="l">records</span></div>
  <div class="stat"><span class="v" id="s_ent">0</span><span class="l">entities</span></div>
  <div class="stat warm" id="frag"><span class="v" id="s_fra">0</span><span class="l">fragile &#9656;</span></div>
  <div class="spacer"></div>
  <div id="slug"><span class="t">match&nbsp;threshold</span>
    <input id="sl" type="range" min="0.60" max="0.95" step="0.005" value="0.82">
    <span id="thv">0.82</span></div>
</div>
<div id="panel" class="glass"></div>
<div id="tip" class="glass"></div>
<div id="hint">drag&nbsp;the&nbsp;threshold &middot; hover&nbsp;a&nbsp;record &middot; click&nbsp;an&nbsp;entity&nbsp;for&nbsp;its&nbsp;evidence</div>
<script>
const DATA=__DATA__;
const N=DATA.n, FIELDS=DATA.fields, REC=DATA.records, PAIRS=DATA.pairs;
const hx=new Float64Array(N), hy=new Float64Array(N);   // clean "home" positions
const px=new Float64Array(N), py=new Float64Array(N), vx=new Float64Array(N), vy=new Float64Array(N);
const ph=new Float64Array(N);                            // per-node shimmer phase
for(let i=0;i<N;i++){hx[i]=DATA.pos[i][0];hy[i]=DATA.pos[i][1];
  px[i]=hx[i];py[i]=hy[i];ph[i]=Math.random()*6.283;}
let TH=parseFloat(document.getElementById('sl').value);

// ---- union-find clustering at the live threshold ----
const par=new Int32Array(N);
function find(x){while(par[x]!=x){par[x]=par[par[x]];x=par[x];}return x;}
let comp=new Int32Array(N), members={}, minedge={}, sizeOf={};
function recluster(){
  for(let i=0;i<N;i++)par[i]=i;
  for(const[a,b,s]of PAIRS){if(s>=TH){const ra=find(a),rb=find(b);if(ra!=rb)par[Math.max(ra,rb)]=Math.min(ra,rb);}}
  members={};minedge={};sizeOf={};
  for(let i=0;i<N;i++){const c=find(i);comp[i]=c;(members[c]||(members[c]=[])).push(i);}
  for(const[a,b,s]of PAIRS){if(s>=TH){const c=comp[a];if(c==comp[b])minedge[c]=Math.min(minedge[c]??9,s);}}
  let ent=0,frag=0;
  for(const c in members){ent++;const m=members[c];sizeOf[c]=m.length;
    if(m.length>=2 && (minedge[c]??1)<TH+0.04)frag++;}
  document.getElementById('s_rec').textContent=N;
  document.getElementById('s_ent').textContent=ent;
  document.getElementById('s_fra').textContent=frag;
  fragList=Object.keys(members).filter(c=>members[c].length>=2&&(minedge[c]??1)<TH+0.04)
    .sort((a,b)=>(minedge[a]??1)-(minedge[b]??1));
}
let fragList=[];
function status(c){const m=members[c]||[];if(m.length>=2&&(minedge[c]??1)<TH+0.04)return'fragile';return'ok';}
function hue(c){return `hsl(${((c*223.6)%360)} 58% 62%)`;}
function nodeColor(i){const c=comp[i];return status(c)=='fragile'?'#ffb15a':hue(c);}

// ---- canvas ----
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
let DPR=Math.max(1,devicePixelRatio||1),view={x:0,y:0,k:1};
function resize(){cv.width=innerWidth*DPR;cv.height=innerHeight*DPR;}
addEventListener('resize',resize);resize();
function fit(){let a=1e9,b=-1e9,c=1e9,d=-1e9;
  for(let i=0;i<N;i++){a=Math.min(a,px[i]);b=Math.max(b,px[i]);c=Math.min(c,py[i]);d=Math.max(d,py[i]);}
  view.k=Math.min((innerWidth-360)/(b-a+1),(innerHeight-220)/(d-c+1));
  view.x=innerWidth/2-(a+b)/2*view.k;view.y=innerHeight/2-(c+d)/2*view.k;}
const SX=x=>x*view.k+view.x, SY=y=>y*view.k+view.y;
let hov=-1, sel=-1, t=0;

function step(){ // hold the clean layout: spring to home + a small living shimmer
  for(let i=0;i<N;i++){
    const tx=hx[i]+Math.cos(ph[i]+t*0.012)*1.6, ty=hy[i]+Math.sin(ph[i]*1.3+t*0.012)*1.6;
    vx[i]=(vx[i]+(tx-px[i])*0.02)*0.90; vy[i]=(vy[i]+(ty-py[i])*0.02)*0.90;
    px[i]+=vx[i]; py[i]+=vy[i];
  }
}
function edgeStroke(s){const f=Math.max(0,Math.min(1,(s-TH)/(1-TH+1e-6)));
  return `rgba(${235-150*f|0},${150+80*f|0},${120+80*f|0},${0.10+0.34*f})`;}

function draw(){
  ctx.setTransform(DPR,0,0,DPR,0,0);
  ctx.fillStyle='#05070b';ctx.fillRect(0,0,innerWidth,innerHeight);
  const g=ctx.createRadialGradient(innerWidth/2,innerHeight/2,0,innerWidth/2,innerHeight/2,Math.max(innerWidth,innerHeight)*.7);
  g.addColorStop(0,'rgba(22,30,48,.55)');g.addColorStop(1,'rgba(5,7,11,0)');
  ctx.fillStyle=g;ctx.fillRect(0,0,innerWidth,innerHeight);
  // edges
  ctx.lineWidth=1;
  for(const[a,b,s]of PAIRS){if(s<TH||comp[a]!=comp[b])continue;
    if(sel>=0 && comp[a]!=sel)continue;
    ctx.strokeStyle=edgeStroke(s);ctx.beginPath();
    ctx.moveTo(SX(px[a]),SY(py[a]));ctx.lineTo(SX(px[b]),SY(py[b]));ctx.stroke();}
  // glowing nodes (additive)
  ctx.globalCompositeOperation='lighter';
  for(let i=0;i<N;i++){const c=comp[i];const fr=status(c)=='fragile';
    const dim=(sel>=0&&c!=sel)?0.28:1;
    const R=(4+1.6*Math.sqrt(Math.max(sizeOf[c]-1,0)))*(fr?1.15:1);
    const x=SX(px[i]),y=SY(py[i]);
    let pulse=fr?(0.7+0.3*Math.sin(t*0.06+c)):1;
    const grd=ctx.createRadialGradient(x,y,0,x,y,R*3.2);
    const col=nodeColor(i);
    grd.addColorStop(0,col);grd.addColorStop(0.4,fr?`rgba(255,170,80,${0.5*dim*pulse})`:`rgba(150,180,230,${0.30*dim})`);
    grd.addColorStop(1,'rgba(0,0,0,0)');
    ctx.globalAlpha=dim*(fr?pulse:1);ctx.fillStyle=grd;
    ctx.beginPath();ctx.arc(x,y,R*3.2,0,7);ctx.fill();}
  ctx.globalCompositeOperation='source-over';ctx.globalAlpha=1;
  // crisp cores
  for(let i=0;i<N;i++){const c=comp[i];const dim=(sel>=0&&c!=sel)?0.3:1;
    ctx.globalAlpha=dim;ctx.fillStyle=status(c)=='fragile'?'#ffd9a8':'#eaf0fb';
    const x=SX(px[i]),y=SY(py[i]),R=Math.max(1.4,(2.2)*Math.min(view.k*1.2,1.6));
    ctx.beginPath();ctx.arc(x,y,R,0,7);ctx.fill();}
  ctx.globalAlpha=1;
  if(hov>=0){const x=SX(px[hov]),y=SY(py[hov]);
    ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,7,0,7);ctx.stroke();}
}
function loop(){t++;step();draw();requestAnimationFrame(loop);}

// ---- interaction ----
function pick(mx,my){let best=-1,bd=11;
  for(let i=0;i<N;i++){const dx=SX(px[i])-mx,dy=SY(py[i])-my,d=Math.hypot(dx,dy);
    if(d<bd){bd=d;best=i;}}return best;}
let drag=null;
cv.addEventListener('mousedown',e=>drag={x:e.clientX,y:e.clientY,mx:view.x,my:view.y,moved:0});
addEventListener('mouseup',()=>{if(drag&&drag.moved<4){const i=pick(lastm.x,lastm.y);
  if(i>=0)selectEntity(comp[i]);else{sel=-1;document.getElementById('panel').style.display='none';}}drag=null;});
let lastm={x:0,y:0};
cv.addEventListener('mousemove',e=>{lastm={x:e.clientX,y:e.clientY};
  if(drag){drag.moved+=Math.abs(e.movementX)+Math.abs(e.movementY);
    view.x=drag.mx+(e.clientX-drag.x);view.y=drag.my+(e.clientY-drag.y);return;}
  const i=pick(e.clientX,e.clientY);hov=i;const tip=document.getElementById('tip');
  if(i>=0){const r=REC[i];let h=FIELDS.map((k,j)=>`<span class="k">${k}</span> ${r[j+1]}`).join('<br>');
    tip.innerHTML=h;tip.style.display='block';
    tip.style.left=Math.min(e.clientX+15,innerWidth-250)+'px';tip.style.top=(e.clientY+14)+'px';}
  else tip.style.display='none';});
cv.addEventListener('wheel',e=>{e.preventDefault();const f=Math.exp(-e.deltaY*0.0012);
  view.x=e.clientX-(e.clientX-view.x)*f;view.y=e.clientY-(e.clientY-view.y)*f;view.k*=f;},{passive:false});

const sl=document.getElementById('sl');
sl.addEventListener('input',()=>{TH=parseFloat(sl.value);document.getElementById('thv').textContent=TH.toFixed(3);
  recluster();if(sel>=0&&!members[sel])sel=-1;if(sel>=0)renderPanel(sel);});

function selectEntity(c){sel=c;renderPanel(c);
  // ease view to the entity centroid
  const m=members[c];let cx=0,cy=0;m.forEach(i=>{cx+=px[i];cy+=py[i];});cx/=m.length;cy/=m.length;
  view.k=Math.max(view.k,2.0);view.x=innerWidth/2-cx*view.k;view.y=innerHeight/2-cy*view.k;}
function renderPanel(c){
  const m=members[c],p=document.getElementById('panel'),st=status(c),me=minedge[c];
  // internal active pairs, weakest first
  const ip=[];let bn=null;
  for(const[a,b,s]of PAIRS){if(s>=TH&&comp[a]==c&&comp[b]==c){ip.push([a,b,s]);if(s==me&&!bn)bn=[a,b];}}
  ip.sort((x,y)=>x[2]-y[2]);
  const recRows=m.map(i=>`<tr><td class="mono">${i}</td>`+FIELDS.map((k,j)=>`<td>${REC[i][j+1]}</td>`).join('')+`</tr>`).join('');
  const ev=ip.map(([a,b,s])=>{const isb=bn&&((a==bn[0]&&b==bn[1])||(a==bn[1]&&b==bn[0]));
    const cl=s<TH+0.04?'weak':'strong';
    return `<tr class="${isb?'bn':''}"><td class="mono">${a}&ndash;${b}</td><td class="mono ${cl}">${s.toFixed(3)}</td><td>${isb?'bottleneck':''}</td></tr>`;}).join('');
  p.innerHTML=`<h2>entity <span class="mono">#${c}</span> <span class="badge ${st}">${st}</span>`+
    `<x onclick="sel=-1;this.parentNode.parentNode.style.display='none'">&times;</x></h2>`+
    `<div class="sub">${m.length} record${m.length>1?'s':''}`+
    (me!=null&&m.length>1?` &middot; weakest link <span class="mono">${me.toFixed(3)}</span> (cutoff ${TH.toFixed(3)})`:'')+`</div>`+
    `<table><tr><th>id</th>${FIELDS.map(k=>`<th>${k}</th>`).join('')}</tr>${recRows}</table>`+
    (ip.length?`<div style="font-size:10px;letter-spacing:.08em;color:#7f8aa0;text-transform:uppercase;margin:12px 0 4px">evidence &middot; weakest first</div>`+
      `<table><tr><th>pair</th><th>score</th><th></th></tr>${ev}</table>`:
      `<div class="sub">singleton at this threshold &mdash; no merge evidence</div>`);
  p.style.display='block';
}
let fragIdx=0;
document.getElementById('frag').addEventListener('click',()=>{if(!fragList.length)return;
  const c=+fragList[fragIdx%fragList.length];fragIdx++;selectEntity(c);});

recluster();fit();loop();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="resolution_atlas.html")
    ap.add_argument("--png", default=None, help="also write a static preview PNG")
    ap.add_argument("--entities", type=int, default=120)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=0.82)
    args = ap.parse_args()

    df = build_audit_dataset(entities=args.entities, seed=args.seed)
    res = dedupe_df(df, fuzzy={"first_name": 0.78, "last_name": 0.78},
                    blocking=["zip"], threshold=FLOOR, confidence_required=False)
    pairs = res.scored_pairs or []
    payload = build_payload(df, pairs, args.threshold)
    print(f"{payload['n']} records, {len(pairs)} scored pairs "
          f"(>= {FLOOR}); default threshold {args.threshold}")
    Path(args.out).write_text(HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":"))),
                              encoding="utf-8")
    print(f"wrote {args.out}  -- open in a browser, drag the threshold")
    if args.png:
        render_png(payload, args.threshold, args.png)


if __name__ == "__main__":
    main()
