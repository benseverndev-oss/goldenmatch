"""Diagnostic for issue #195 — auto-config degenerates at 2M+ rows.

Loads two synthetic fixtures (1M, 2M) reusing the scale-audit harness's
fixture generator, runs `auto_configure_df(..., _skip_finalize=True)` on
each, and prints a side-by-side summary of:

  - controller history's stop_reason
  - committed config matchkeys (type + fields + threshold)
  - blocking rules
  - profile health verdict

The 1M run produced ~145K multi-member clusters; the 2M run produced
~2,570. Same fixture shape, same code, dramatically different output —
this script gets us the committed-config diff that explains it.

Usage:
    python scripts/investigate_autoconfig_2m.py

Exits 0 always. Prints results to stdout.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO / ".profile_tmp" / "scale_fixtures"


def ensure_fixture(rows: int, dupe_rate: float = 0.15) -> Path:
    sys.path.insert(0, str(REPO / "packages" / "python" / "goldenmatch"))
    from tests.generate_synthetic import generate  # type: ignore[import-not-found]

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"synthetic_{rows}_dupe{int(dupe_rate*100):02d}.csv"
    if not path.exists():
        print(f"[fixture] generating {rows:,} rows -> {path.name}", file=sys.stderr)
        generate(path, n_records=rows, dupe_rate=dupe_rate)
    return path


def dump_config(label: str, rows: int) -> None:
    from goldenmatch.core.autoconfig import auto_configure_df, _LAST_CONTROLLER_RUN

    fp = ensure_fixture(rows)
    df = pl.read_csv(fp, encoding="utf8-lossy", ignore_errors=True)

    t0 = time.perf_counter()
    cfg = auto_configure_df(df, _skip_finalize=True)
    elapsed = time.perf_counter() - t0

    state = _LAST_CONTROLLER_RUN.get()
    history = state[1] if state is not None else None
    profile = state[0] if state is not None else None

    print()
    print(f"=== {label} (rows={rows:,}) ===")
    print(f"auto_configure wall: {elapsed:.2f} s")
    print(f"stop_reason:         {getattr(history, 'stop_reason', None)}")
    print(f"profile.health():    {profile.health().value if profile else None}")
    print(f"iterations recorded: {len(history.decisions) if history else 0}")
    print()
    print("matchkeys:")
    for mk in cfg.matchkeys or []:
        print(f"  - type={mk.type}")
        if mk.type == "weighted":
            print(f"    threshold={mk.threshold}")
            for f in mk.fields or []:
                print(f"    field={f.field} scorer={f.scorer} weight={f.weight}")
        elif mk.type == "exact":
            for f in mk.fields or []:
                print(f"    field={f.field}")
        elif mk.type == "fuzzy":
            print(f"    field={mk.field} threshold={mk.threshold} scorer={mk.scorer}")
    print()
    print("blocking:")
    blk = getattr(cfg, "blocking", None)
    if blk is None:
        print("  (none)")
    else:
        print(f"  strategy={getattr(blk, 'strategy', None)}")
        for k in (getattr(blk, "keys", None) or []):
            print(f"  key: field={getattr(k, 'field', None)} transforms={getattr(k, 'transforms', None)}")
    print()
    print("controller history.decisions (first 5):")
    for d in (history.decisions if history else [])[:5]:
        # repr may contain non-cp1252 chars (e.g. unicode arrows from rule
        # rationales). Encode-safe on Windows terminals via ASCII fallback.
        msg = f"  - {d}".encode("ascii", "replace").decode("ascii")
        print(msg)
    print()
    print("per-entry ranking stats (the pick_committed() input):")
    print("  iter | health  | mass_above | mass_borderline | sep   | -sep   | (rank, -sep, iter) lex_key")
    if history:
        from goldenmatch.core.autoconfig_history import HealthVerdict
        for e in history.entries:
            sp = e.profile.scoring
            verdict = e.profile.health()
            rank = {HealthVerdict.GREEN: 0, HealthVerdict.YELLOW: 1, HealthVerdict.RED: 2}[verdict]
            sep = sp.mass_above_threshold - sp.mass_in_borderline
            if verdict == HealthVerdict.RED and sp.mass_above_threshold > 0.9:
                rank = 3
            key_tuple = (rank, -sep, e.iteration)
            print(
                f"  {e.iteration:>4} | {verdict.value:7s} | {sp.mass_above_threshold:9.4f} "
                f"| {sp.mass_in_borderline:14.4f} | {sep:5.3f} | {-sep:6.3f} | {key_tuple}",
            )
        committed = history.pick_committed(precision_collapse_floor=0.9)
        if committed is not None:
            print(f"  -> committed iteration={committed.iteration}")
    print()


def main() -> int:
    for label, rows in [("1M-baseline", 1_000_000), ("2M-degenerate", 2_000_000)]:
        try:
            dump_config(label, rows)
        except BaseException as exc:
            import traceback
            print(f"[error] {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
