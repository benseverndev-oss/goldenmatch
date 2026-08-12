"""Combine the two independently-measured bench arms into one comparison.

A separate script rather than inline Python in the workflow: an embedded
multi-line program inside a YAML `run:` block has to survive block-scalar
parsing as well as Python's, and this repo has now broken that twice.

Each arm is measured in its own PROCESS (see bench_spark_scoring.py) because
`local[*]` shares one JVM between driver and executor -- so running both in one
session left the second arm starting on the first's residue, and it OOM'd. This
script only reads their JSON.
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    row_path, jvm_path, out_path = argv[0], argv[1], argv[2]

    a = json.load(open(row_path, encoding="utf-8"))
    b = json.load(open(jvm_path, encoding="utf-8"))
    ra = a["timing"]["median_s"]
    rb = b["timing"]["median_s"]

    print("=" * 68)
    print(f"RESULT   rows={a['rows']:,}   candidate_pairs={a['candidate_pairs']:,}")
    print("=" * 68)
    for name, r in (("row_python", a["timing"]), ("batched_jvm", b["timing"])):
        print(
            f"  {name:14} median {r['median_s']:8.3f}s   "
            f"(min {r['min_s']:.3f} max {r['max_s']:.3f})   "
            f"rows_out={r['rows_out']:,}"
        )

    ratio = (ra / rb) if rb else None
    print()
    if ratio is None:
        print("  INCONCLUSIVE: batched_jvm reported zero elapsed time.")
    else:
        print(f"  batched_jvm is {ratio:.2f}x the row_python path")
        print()
        if ratio < 1.2:
            print("  The Python-worker hop is NOT where the time goes. J2's JNI")
            print("  work should be re-justified on grounds other than throughput")
            print("  -- the one-kernel and no-Python-on-executors arguments still")
            print("  stand, the speed one does not.")
        else:
            print("  The Python-worker hop dominates. Removing the interpreter as")
            print("  well as the hop (J2, JNI into score-cabi) is worth building.")

    print()
    print("  CAVEAT, load-bearing: batched_jvm scores `exact` while row_python")
    print("  scores jaro_winkler, because the J0 jar carries no algorithms of its")
    print("  own. This measures the CALLING MECHANISM, not the kernels -- and it")
    print("  flatters batched_jvm LEAST, since `exact` does almost no work and")
    print("  leaves per-call overhead as the whole signal. Like-for-like needs J2.")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"row_python": a, "batched_jvm": b, "ratio": ratio}, fh, indent=2)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
