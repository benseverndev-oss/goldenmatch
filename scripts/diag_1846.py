"""#1846 diagnostic: why does historical_50k f1_probabilistic collapse on CI/Linux?

Measured so far -- every one of these PASSES (~0.82):

  * local Windows, native=0 / native=1 / auto, memory=0 / memory=1  (4 combos)
  * local Windows, main HEAD and every commit back through #1826      (5 rungs)

CI/Linux on the SAME commit with the SAME flags: 0.3335. #1847 proves it needs
no code change at all -- it changes only ci.yml/filters.yml and still fails --
so this is main + Linux, not any PR and not any of the commits bisected.

historical_50k is the ONLY dataset >= 50,000 rows, so it is the only one that
trips the learned-blocking upgrade. Every smaller dataset is clean on both
platforms. That is the sharpest clue we have.

This dumps the same facts on both platforms so the DIFF names the cause instead
of another hypothesis. Prefix every line with DIAG1846 so it greps cleanly out
of a CI log.

Run: python scripts/diag_1846.py
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "python" / "goldenmatch"))
sys.path.insert(0, str(ROOT))

P = "DIAG1846"


def out(k: str, v: object) -> None:
    print(f"{P} {k}={v}", flush=True)


def main() -> int:
    # ---- host / deps -------------------------------------------------------
    out("os", platform.system())
    out("machine", platform.machine())
    out("python", platform.python_version())
    out("cpu_count", os.cpu_count())
    out("env.GOLDENMATCH_NATIVE", os.environ.get("GOLDENMATCH_NATIVE", "<unset>"))
    out("env.GOLDENMATCH_AUTOCONFIG_MEMORY",
        os.environ.get("GOLDENMATCH_AUTOCONFIG_MEMORY", "<unset>"))

    for mod in ("polars", "numpy", "pyarrow", "jellyfish", "recordlinkage"):
        try:
            m = __import__(mod)
            out(f"dep.{mod}", getattr(m, "__version__", "?"))
        except Exception as e:
            out(f"dep.{mod}", f"MISSING ({type(e).__name__})")

    # jellyfish drives _safe_soundex; without it every soundex blocking key
    # silently degrades to val[:4].upper() -> different blocks -> different recall.
    try:
        import jellyfish
        out("soundex.jellyfish('Smith')", jellyfish.soundex("Smith"))
    except Exception:
        out("soundex.jellyfish", "UNAVAILABLE -> _safe_soundex falls back to val[:4].upper()")
    from goldenmatch.core.learned_blocking import _safe_soundex
    out("soundex.effective('Smith')", _safe_soundex("Smith"))

    # ---- native ------------------------------------------------------------
    from goldenmatch.core._native_loader import native_enabled, native_module
    try:
        out("native.module", getattr(native_module(), "__file__", "<none>"))
    except Exception as e:
        out("native.module", f"UNAVAILABLE ({type(e).__name__}: {e})")
    for cap in ("block_scoring", "fs_bucket", "autoconfig", "scoring"):
        try:
            out(f"native.{cap}", native_enabled(cap))
        except Exception as e:
            out(f"native.{cap}", f"ERR {type(e).__name__}")

    # ---- the committed config for historical_50k ---------------------------
    import polars as pl
    p = ROOT / "scripts" / "autoconfig_quality" / "vendored" / "historical_50k.parquet"
    if not p.exists():
        out("dataset", "ABSENT -- cannot diagnose")
        return 0
    df = pl.read_parquet(p)
    df = df.drop([c for c in ("cluster", "unique_id") if c in df.columns])
    out("rows", df.height)
    out("cols", df.columns)

    from goldenmatch.core.autoconfig import auto_configure_df
    cfg = auto_configure_df(df)
    blk = cfg.blocking
    out("cfg.backend", getattr(cfg, "backend", None))
    out("cfg.blocking.strategy", getattr(blk, "strategy", None))
    out("cfg.blocking.keys", [k.fields for k in (getattr(blk, "keys", None) or [])])
    out("cfg.blocking.passes", [p_.fields for p_ in (getattr(blk, "passes", None) or [])])
    out("cfg.blocking.max_block_size", getattr(blk, "max_block_size", None))
    out("cfg.blocking.skip_oversized", getattr(blk, "skip_oversized", None))
    out("cfg.matchkeys", [(m.name, m.type, [f.field for f in m.fields]) for m in cfg.matchkeys])

    from goldenmatch.core.blocker import collect_blocking_fields
    out("cfg.em_blocking_fields", collect_blocking_fields(blk))

    # Which scorer will actually run -- the routing decision the gate depends on.
    try:
        from goldenmatch.core.pipeline import _use_bucket_scorer
        out("routing.use_bucket_scorer", _use_bucket_scorer(cfg, df.head(1000)))
    except Exception as e:
        out("routing.use_bucket_scorer", f"ERR {type(e).__name__}: {e}")

    # ---- blocking outcome --------------------------------------------------
    from goldenmatch.core.blocker import build_blocks
    lf = df.with_row_index("__row_id__").lazy()
    try:
        blocks = build_blocks(lf, blk)
        sizes = [b.n_rows() for b in blocks]
        out("blocks.count", len(blocks))
        out("blocks.total_rows", sum(sizes))
        out("blocks.max_size", max(sizes) if sizes else 0)
        out("blocks.pairs", sum(s * (s - 1) // 2 for s in sizes))
        cap = getattr(blk, "max_block_size", 0) or 0
        out("blocks.over_cap", sum(1 for s in sizes if s > cap))
    except Exception as e:
        out("blocks", f"ERR {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
