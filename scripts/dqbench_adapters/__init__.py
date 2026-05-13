"""Committed DQbench adapters and benchmark-evaluation helpers.

These were previously kept under the gitignored `.profile_tmp/` directory,
which meant `scripts/run_benchmarks.py` could not reproduce published
numbers from a fresh `git clone`. They live here now so the runner is
self-contained.

Modules:
  goldenmatch_zeroconfig
    Zero-config DQbench `EntityResolutionAdapter` (used to measure the
    v1.12 DQbench composite of 91.04).
  febrl3
    Ground-truth loader + pair extractor for the synthetic Febrl3
    dataset bundled with `recordlinkage`.
  ncvr
    Ground-truth loader for the 10k-row NCVR voter sample.
  leipzig_eval
    Shared helpers for the Leipzig DBLP-ACM benchmark — joins emitted
    pairs back to the original source IDs (DBLP / ACM) rather than the
    positional row indices in the concatenated frame.
"""
