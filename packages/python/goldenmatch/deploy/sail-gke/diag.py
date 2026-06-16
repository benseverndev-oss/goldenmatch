"""Stage-isolation probe for the Sail-on-GKE hang. score_and_dedup was shown
fast/scaling; this times WCC and golden too, at increasing N, to find which
stage wedges and at what scale. Mounted via ConfigMap (editable, no rebuild)."""
import os
import time

from goldenmatch.sail.clustering import connected_components
from goldenmatch.sail.golden import build_golden
from goldenmatch.sail.scoring import score_and_dedup
from goldenmatch.sail.session import connect

remote = os.environ.get(
    "SAIL_REMOTE", "sc://sail-spark-server.sail.svc.cluster.local:50051"
)
spark = connect(remote)
print("DIAG connected", flush=True)
base = spark.read.parquet("/data/smoke/part-00.parquet")

for n in (1500, 4000, 12000, 37500):
    print(f"DIAG ===== N={n} =====", flush=True)
    src = base.limit(n)
    ids = src.select("__row_id__")

    t = time.perf_counter()
    pairs = score_and_dedup(
        src, block_col="last_name_soundex", value_col="last_name",
        id_col="__row_id__", scorer_name="jaro_winkler", threshold=0.85,
    )
    npairs = pairs.count()
    print(f"DIAG  score   pairs={npairs} t={time.perf_counter()-t:.1f}s", flush=True)

    t = time.perf_counter()
    asg = connected_components(pairs, ids, id_col="__row_id__")
    nasg = asg.count()
    print(f"DIAG  wcc     assignments={nasg} t={time.perf_counter()-t:.1f}s", flush=True)

    t = time.perf_counter()
    golden = build_golden(
        asg, src, value_cols=["first_name", "email"],
        source_id_col="__row_id__", strategy="most_complete",
    )
    ngold = golden.count()
    print(f"DIAG  golden  count={ngold} t={time.perf_counter()-t:.1f}s", flush=True)

print("DIAG DONE", flush=True)
