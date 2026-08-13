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

    pure = json.load(open(pure_path, encoding="utf-8"))
    native = json.load(open(native_path, encoding="utf-8"))
    jvm = json.load(open(jvm_path, encoding="utf-8"))

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
                "like_for_like": like_for_like,
                "native_speedup": (rp / rn) if rn else None,
                "jvm_vs_pure": (rp / rj) if rj else None,
                "jvm_vs_native": (rn / rj) if (rj and rn) else None,
            },
            fh,
            indent=2,
        )
    print("wrote " + out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
