"""VmRSS(current) + HWM at each FS prep boundary -- isolate WHERE the FS peak
is reached (SN materialize vs EM train vs score_buckets)."""
import os
import resource
import sys
from pathlib import Path

os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL","system")
os.environ.update(GOLDENMATCH_AUTOCONFIG_MEMORY="0", GOLDENMATCH_NATIVE="1",
                  GOLDENMATCH_FS_NATIVE="1", GOLDENMATCH_FS_CALIBRATED="posterior",
                  GOLDENMATCH_FS_BLOCKING_SN_BOUND="1", GOLDENMATCH_FS_EM_SAMPLE_ROWS="100000")
path = sys.argv[1]
def rss():
    cur=0
    for l in Path("/proc/self/status").read_text().splitlines():
        if l.startswith("VmRSS:"): cur=int(l.split()[1])/1024
    hwm=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    return cur,hwm
def mark(tag):
    c,h=rss(); print(f"  [{tag:28s}] VmRSS={c:6.0f}MB  HWM={h:6.0f}MB", flush=True)

import goldenmatch.backends.score_buckets as sb
import goldenmatch.core.blocker as blk
import goldenmatch.core.probabilistic as prob
import pyarrow.parquet as pq

try: from goldenmatch import dedupe_df
except ImportError: from goldenmatch._api import dedupe_df

_sn=blk.materialize_sn_passes
def sn_wrap(*a,**k):
    mark("SN-materialize BEFORE"); r=_sn(*a,**k); mark("SN-materialize AFTER"); return r
blk.materialize_sn_passes=sn_wrap
import goldenmatch.core.pipeline as pl_mod

if hasattr(pl_mod,"materialize_sn_passes"): pl_mod.materialize_sn_passes=sn_wrap

_te=prob.train_em
def te_wrap(*a,**k):
    mark("train_em BEFORE"); r=_te(*a,**k); mark("train_em AFTER"); return r
prob.train_em=te_wrap
# load_or_train_em may call the module ref; patch there too
if hasattr(prob,"load_or_train_em"):
    _lot=prob.load_or_train_em
    def lot_wrap(*a,**k):
        mark("load_or_train_em BEFORE"); r=_lot(*a,**k); mark("load_or_train_em AFTER"); return r
    prob.load_or_train_em=lot_wrap
    if hasattr(pl_mod,"load_or_train_em"): pl_mod.load_or_train_em=lot_wrap

_sbk=sb.score_buckets
def sbk_wrap(*a,**k):
    mark("score_buckets BEFORE"); r=_sbk(*a,**k); mark("score_buckets AFTER"); return r
sb.score_buckets=sbk_wrap
if hasattr(pl_mod,"score_buckets"): pl_mod.score_buckets=sbk_wrap

df=pq.read_table(path)
from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

mark("after read_table")
cfg=auto_configure_probabilistic_df(df)
for mk in cfg.get_matchkeys():
    if getattr(mk,'type',None)=="weighted": mk.rerank=False
mark("after auto_configure")
dedupe_df(df, config=cfg)
mark("after dedupe_df")
