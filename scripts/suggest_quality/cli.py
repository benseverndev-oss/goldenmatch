"""Config-suggestion quality harness CLI.

    python -m scripts.suggest_quality report   # enumerate datasets + stub metrics
    python -m scripts.suggest_quality gate     # exit nonzero on regression (CI)
    python -m scripts.suggest_quality bless    # accept current as the baseline

Flags:
    --datasets a,b  filter to a subset of registered datasets
    --row-cap N     oracle row cap (default 20 000); ignored for full_scan datasets
    --native {0,1,auto}  GOLDENMATCH_NATIVE for this run
    --tolerance F   delta-F1 floor band for the gate (default 0.01)

Task 14 scope:
    ``report`` loads each dataset, prints name + row count + GT pair count
    (or ``SKIPPED: <reason>``), and prints a placeholder metrics line.
    ``gate`` and ``bless`` are wired but not yet implemented (Task 15/16).

Determinism: set before any goldenmatch import — mirrors autoconfig_quality.
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
from pathlib import Path

# Polars CPU probe can hang on Windows; set before anything imports polars.
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_BASELINE = Path(__file__).resolve().parent / "baselines" / "scorecard.json"


# ── determinism helper ────────────────────────────────────────────────────────

def _pin_determinism(native: str | None = None) -> None:
    """Pin the determinism environment before any goldenmatch import.

    Mirrors autoconfig_quality's approach exactly:
    - GOLDENMATCH_AUTOCONFIG_MEMORY=0  disables cross-run memory (CI-safe)
    - PYTHONHASHSEED=0                 stable dict iteration
    - POLARS_SKIP_CPU_CHECK=1          avoid Windows WMI hang
    - GOLDENMATCH_NATIVE               passed as --native flag value, if given

    Called once at the top of main() before deferred imports.
    """
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
    if native is not None:
        os.environ["GOLDENMATCH_NATIVE"] = native


# ── run loop ──────────────────────────────────────────────────────────────────

def run(
    dataset_names: set[str] | None,
    row_cap: int | None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Load the corpus -> (results, skipped).

    Heavy imports are deferred so ``--native`` can set GOLDENMATCH_NATIVE
    before goldenmatch loads.  Same pattern as autoconfig_quality.run().

    Task 14: results records carry only ``kind`` + ``rows`` + ``gt_pairs``.
    Task 15 will add the oracle + metrics (before_f1, after_f1, delta).
    """
    from scripts.suggest_quality.datasets import REGISTRY, effective_row_cap  # noqa: PLC0415

    results: dict[str, dict] = {}
    skipped: dict[str, str] = {}

    for d in REGISTRY:
        if dataset_names and d.name not in dataset_names:
            continue
        try:
            loaded = d.loader()
        except Exception as e:  # loader failure -> skip with reason, never crash
            skipped[d.name] = f"loader_error: {e}"
            continue
        if loaded is None:
            skipped[d.name] = "absent"
            continue
        df, gt = loaded
        cap = effective_row_cap(d, row_cap)
        results[d.name] = {
            "kind": d.kind,
            "rows": min(df.height, cap) if cap is not None else df.height,
            "gt_pairs": len(gt),
        }
        del loaded, df, gt
        gc.collect()

    return results, skipped


# ── scorecard helpers (stubs — Task 15 wires the oracle + metrics) ────────────

def _gather_meta() -> tuple[str, str]:
    """(native_version, git_sha) — best-effort, never raises."""
    try:
        import goldenmatch_native  # noqa: PLC0415
        native_version = getattr(goldenmatch_native, "__version__", "unknown")
    except Exception:
        native_version = "absent"
    try:
        import subprocess  # noqa: PLC0415
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        git_sha = "unknown"
    return native_version, git_sha


def _render_report_table(
    results: dict[str, dict], skipped: dict[str, str]
) -> str:
    """Print a simple summary table (no metrics yet — Task 15 adds them)."""
    lines: list[str] = []
    for name, rec in results.items():
        gt = rec["gt_pairs"]
        rows = rec["rows"]
        kind = rec["kind"]
        lines.append(f"  {name:<30} {kind:<7}  rows={rows:<7}  gt_pairs={gt}")
    for name, reason in skipped.items():
        lines.append(f"  {name:<30} SKIPPED: {reason}")
    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="suggest_quality")
    p.add_argument(
        "mode", nargs="?", default="report",
        choices=["report", "gate", "bless"],
    )
    p.add_argument("--datasets", default="", help="comma-separated dataset filter")
    p.add_argument("--row-cap", type=int, default=20_000, help="oracle row cap")
    p.add_argument(
        "--native", choices=["0", "1", "auto"], default=None,
        help="GOLDENMATCH_NATIVE for this run",
    )
    p.add_argument(
        "--tolerance", type=float, default=0.01,
        help="delta-F1 floor band for the gate",
    )
    args = p.parse_args(argv)

    # Pin determinism env BEFORE any goldenmatch import.
    _pin_determinism(args.native)

    names: set[str] | None = {s for s in args.datasets.split(",") if s} or None
    results, skipped = run(names, args.row_cap)
    native_version, git_sha = _gather_meta()

    if args.mode == "report":
        print("suggest_quality report")
        print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
        print()
        print(_render_report_table(results, skipped))
        print()
        print(f"  {len(results)} dataset(s) loaded, {len(skipped)} skipped")
        print("  0 metrics yet (Task 15 wires the oracle and delta-F1 measurement)")
        return 0

    if args.mode == "bless":
        print("suggest_quality bless: not implemented until Task 16")
        print("(Task 16 will wire the oracle, build the scorecard, and commit the baseline.)")
        return 0

    if args.mode == "gate":
        print("suggest_quality gate: not implemented until Task 15/16")
        print("(Task 15 wires the oracle; Task 16 adds the CI gate against the bless'd baseline.)")
        # Exit 0 (never fail) until the gate is implemented.
        return 0

    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
