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
    pure_path, native_path, jvm_path, out_path = argv[0], argv[1], argv[2], argv[3]
    # Optional 5th arm: the row-shaped JVM control. Optional because it was
    # added after the fact and a dispatch of an older ref must still compare.
    row_jvm_path = argv[4] if len(argv) > 4 else None

    pure = json.load(open(pure_path, encoding="utf-8"))
    native = json.load(open(native_path, encoding="utf-8"))
    jvm = json.load(open(jvm_path, encoding="utf-8"))
    row_jvm = None
    if row_jvm_path:
        try:
            row_jvm = json.load(open(row_jvm_path, encoding="utf-8"))
        except FileNotFoundError:
            row_jvm = None

    print("=" * 70)
    print(f"RESULT   rows={pure['rows']:,}   "
          f"candidate_pairs={pure['candidate_pairs']:,}")
    print("=" * 70)
    arms = [
        ("row_python (pure)", pure),
        ("row_python (NATIVE)", native),
        (f"batched_jvm ({jvm.get('scorer', '?')})", jvm),
    ]
    for name, a in arms:
        r = a["timing"]
        # `native_resolved` is the pyo3 loader's state ON THE DRIVER, which says
        # something about the row_python arms and NOTHING about the JVM arm --
        # that one reaches the kernel through JNI, and printing `False` next to
        # it reads as "the kernel did not run" when it did. Report what actually
        # answers the question for each arm.
        if a.get("path") == "batched_jvm":
            tag = f"  impl={a.get('jvm_impl', '?')}"
        else:
            res = a.get("native_resolved")
            tag = "" if res is None else f"  native_resolved={res}"
        print(f"  {name:22} median {r['median_s']:8.3f}s   "
              f"(min {r['min_s']:.3f} max {r['max_s']:.3f})   "
              f"rows_out={r['rows_out']:,}{tag}")

    print()
    # THE claim in spec section 1, finally measured: what does the Rust kernel
    # buy over the pure floor, holding everything else identical?
    rp, rn = pure["timing"]["median_s"], native["timing"]["median_s"]
    if not native.get("native_resolved"):
        print("  WARNING: the native arm did NOT resolve to the Rust kernel.")
        print("  The wheel is missing from the driver or, more likely, from the")
        print("  SHIPPED EXECUTOR ENV -- the UDF runs there. Treat the two")
        print("  row_python numbers as the same measurement taken twice.")
    elif rn:
        print(f"  Rust kernel vs pure floor: {rp / rn:.2f}x")
        print("  (same UDF, same plan, same worker -- only the kernel differs,")
        print("   so this is the section-1 differentiator on its own.)")

    rj = jvm["timing"]["median_s"]
    # The comparison this arc was built for. Both arms now run the same scorer
    # (J2 removed the `exact`-only restriction), so the ONLY difference left is
    # where the kernel runs: in a forked Python worker behind an Arrow
    # serialisation hop, or in the executor JVM behind a JNI downcall.
    like_for_like = pure.get("scorer") == jvm.get("scorer")
    if rj and like_for_like:
        print(f"  batched_jvm vs row_python (pure):   {rp / rj:.2f}x")
        if rn:
            print(f"  batched_jvm vs row_python (NATIVE): {rn / rj:.2f}x")
            print("  (the honest one: same kernel both sides, so the ratio is")
            print("   the HOP -- Arrow serialise + fork + interpreter -- alone.)")
    elif rj:
        print(f"  batched_jvm vs pure row_python: {rp / rj:.2f}x")
        print(f"  NOT like-for-like: batched_jvm ran {jvm.get('scorer')!r}, "
              f"row_python ran {pure.get('scorer')!r}.")

    # A threshold that rejects NOTHING makes the batched arm's whole reason for
    # having one inert, and says so in a number nobody would look at.
    #
    # Run 31709489001 was dispatched at 0.85 to test exactly that lever and came
    # back with `rows_out` unchanged at 1,900,000 -- 100% kept. The synthetic
    # fixture puts near-identical strings in a block ("v5_3" vs "v5_7"), which
    # jaro-winkler scores around 0.88, so 0.85 rejects nothing and the run
    # measured the un-filtered plan twice. The medians looked plausible and the
    # comparison was meaningless.
    #
    # So the harness states the reject ratio rather than leaving it to be
    # inferred from a row count.
    thr = jvm.get("threshold") or 0.0
    kept = None
    pairs = jvm.get("candidate_pairs") or 0
    out_rows = (jvm.get("timing") or {}).get("rows_out")
    if pairs and out_rows is not None:
        kept = out_rows / pairs
        print()
        print(f"  threshold={thr}: kept {out_rows:,}/{pairs:,} pairs ({kept:.1%})")
    if thr > 0 and kept is not None and kept > 0.999:
        print()
        print("  WARNING: the threshold rejected nothing, so the batched arm's")
        print("  filter-before-explode did no work and this run says NOTHING")
        print("  about it. Raise the threshold until pairs are actually")
        print("  rejected, or the comparison is the threshold=0 one again.")

    # THE decomposition. batched_jvm and row_jvm run the same kernel in the same
    # JVM and differ only in plan shape, so the gap between them is J1's reshape
    # (collect_list + arrays_zip + explode) and nothing else. The gap between
    # row_jvm and row_python is then the mechanism alone -- JNI downcall per
    # pair versus a columnar Arrow hop to a forked worker -- measured on
    # identical plans.
    #
    # J1 batched on the assertion that a per-row downcall "would be dominated by
    # call overhead". These two numbers are what that assertion was missing.
    if row_jvm:
        rr = row_jvm["timing"]["median_s"]
        print()
        print(f"  row_jvm (same plan as row_python, scorer in the JVM): {rr:.3f}s")
        if rj:
            print(f"    J1's reshape costs:      {rj - rr:+.3f}s  "
                  f"(batched_jvm {rj:.3f}s - row_jvm {rr:.3f}s)")
        if rn:
            print(f"    the JVM mechanism costs: {rr - rn:+.3f}s  "
                  f"(row_jvm {rr:.3f}s - row_python NATIVE {rn:.3f}s)")
        if rn and rr < rn:
            print("    row_jvm BEATS the native Python arm: batching was the cost.")

    impl = jvm.get("jvm_impl")
    if impl and impl != "NativeScorer":
        print()
        print(f"  WARNING: the JVM arm resolved {impl}, not NativeScorer -- it")
        print("  fell back to the J0 `exact`-only scorer and this ratio is not")
        print("  measuring the kernel.")
    if jvm.get("jvm_runtime"):
        print()
        print(f"  executor JVM: {jvm['jvm_runtime']}")
        print("  (heap matters: the batched path materialises each group as an")
        print("   array in JVM heap, so its ceiling is a property of the")
        print("   executor rather than of the design. An OOM at an unknown heap")
        print("   size measures a configuration, not an architecture.)")

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "row_python_pure": pure,
                "row_python_native": native,
                "batched_jvm": jvm,
                "row_jvm": row_jvm,
                "like_for_like": like_for_like,
                "threshold": thr,
                "kept_fraction": kept,
                "native_speedup": (rp / rn) if rn else None,
                "jvm_vs_pure": (rp / rj) if rj else None,
                "jvm_vs_native": (rn / rj) if (rj and rn) else None,
                "row_jvm_vs_native": (
                    rn / row_jvm["timing"]["median_s"] if row_jvm and rn else None
                ),
                "reshape_cost_s": (
                    rj - row_jvm["timing"]["median_s"] if row_jvm and rj else None
                ),
            },
            fh,
            indent=2,
        )
    print("wrote " + out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
