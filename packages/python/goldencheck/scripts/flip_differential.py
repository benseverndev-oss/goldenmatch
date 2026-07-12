"""Flip §8b differential measurement — authoritative 2.x-Polars vs owned-fused.

Stage 1 of the Flip. Runs each corpus dataset through BOTH scan paths and
reports the finding-set delta per §8b:
  (1) finding-set Jaccard + count delta per (check, severity)
  (2) stat-threshold-flip bucket   (statrs p-value crossed a cutoff scipy didn't)
  (3) owned-sample-flip bucket      (finding differs only because sampled rows differ)
  (4) inferred_type string diffs
  (5) max stat-float delta per stat family
  (6) DatasetProfile.health_score grade delta (secondary invariant)

Acceptance (§8b): non-stat, non-sample findings MUST be identical (Jaccard 1.0).
Any delta there is a KERNEL BUG that blocks the Flip.

Run via the main venv with the worktree on PYTHONPATH:
  .venv/Scripts/python.exe scripts/flip_differential.py [--fused] [--corpus DIR]

Without --fused it captures ONLY the authoritative side (P) and validates the
corpus triggers findings across families (a smoke gate before the fused side
exists). With --fused it runs both and emits the report.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from goldencheck.core.frame import dtype_category

_FLOAT_RE = re.compile(r"-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+(?:[eE][+-]?\d+)?")

# check families that are statistical (statrs/scipy) -- their threshold flips
# are an accepted divergence class, held apart from the strict-identity set.
STAT_CHECKS = {
    "correlation",
    "cross_column",
    "benford",
    "distribution",
    "statistical_baseline",
    "drift",
}


def _finding_key(f) -> tuple:
    """Differential key: identity fields PLUS the count where numeric divergence surfaces."""
    return (f.check, f.column, int(f.severity), f.affected_rows)


def _finding_full(f) -> dict:
    d = asdict(f)
    d["severity"] = int(f.severity)
    # normalize sample order (kernel vs polars may emit a different in-group order)
    d["sample_values"] = sorted(str(v) for v in (f.sample_values or []))
    return d


def run_authoritative(path: Path) -> tuple[list, dict]:
    """The current 2.x-Polars authoritative scan (PolarsColumn)."""
    from goldencheck.engine.scanner import scan_file

    findings, profile = scan_file(path, baseline=None)
    prof = {
        "columns": {c.name: c.inferred_type for c in profile.columns},
    }
    return findings, prof


# The seam-clean COLUMN profilers -- their bodies route through the Frame/Column
# seam (to_frame -> frame.column(name) -> col.<method>()), so an ArrowFrame drives
# them polars-free with no profiler change. These are the ones the Flip fuses.
def _seam_profilers():
    from goldencheck.profilers.cardinality import CardinalityProfiler
    from goldencheck.profilers.freshness import FreshnessProfiler
    from goldencheck.profilers.nullability import NullabilityProfiler
    from goldencheck.profilers.range_distribution import RangeDistributionProfiler
    from goldencheck.profilers.sequence_detection import SequenceDetectionProfiler
    from goldencheck.profilers.type_inference import TypeInferenceProfiler
    from goldencheck.profilers.uniqueness import UniquenessProfiler

    # Order mirrors COLUMN_PROFILERS in engine/scanner.py (subset that is seam-clean).
    return [
        TypeInferenceProfiler(),
        NullabilityProfiler(),
        UniquenessProfiler(),
        RangeDistributionProfiler(),
        CardinalityProfiler(),
        SequenceDetectionProfiler(),
        FreshnessProfiler(),
    ]


def _run_seam(frame, columns: list[str]) -> list:
    """Run the seam-clean column profilers over one frame (PolarsFrame or
    ArrowFrame), matching the scanner's per-column loop + shared context dict."""
    profilers = _seam_profilers()
    findings: list = []
    context: dict = {}
    for name in columns:
        for prof in profilers:
            try:
                findings.extend(prof.profile(frame, name, context=context))
            except Exception as e:  # noqa: BLE001 - surface as a captured error, not a crash
                findings.append(_ErrorFinding(prof.__class__.__name__, name, repr(e)))
    return findings


class _ErrorFinding:
    """Sentinel so a profiler crash on one side shows up as a strict-set diff
    rather than silently vanishing."""

    def __init__(self, profiler: str, column: str, err: str) -> None:
        self.check = f"__error__:{profiler}"
        self.column = column
        self.severity = 3
        self.affected_rows = -1
        self.message = err
        self.sample_values: list = []


def run_authoritative_seam(path: Path):
    """Authoritative side of the fused diff: seam profilers over a PolarsFrame."""
    import polars as pl

    from goldencheck.core.frame import PolarsFrame

    df = pl.read_parquet(path)
    frame = PolarsFrame(df)
    return _run_seam(frame, df.columns), {c: dtype_category(df[c].dtype) for c in df.columns}, \
        {c: str(df[c].dtype) for c in df.columns}


def run_fused_seam(path: Path):
    """Fused side of the diff: the SAME seam profilers over an ArrowFrame."""
    import pyarrow.parquet as pq

    from goldencheck.core.frame import ArrowFrame

    tbl = pq.read_table(path)
    frame = ArrowFrame(tbl)
    cols = tbl.column_names
    return _run_seam(frame, cols), {c: frame.column(c).dtype for c in cols}, \
        {c: frame.column(c).dtype_repr() for c in cols}


def _summarize(name: str, findings: list) -> dict:
    by_check: dict[str, int] = {}
    for f in findings:
        by_check[f.check] = by_check.get(f.check, 0) + 1
    return {"dataset": name, "n_findings": len(findings), "by_check": by_check}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fused", action="store_true", help="run the owned-fused side + diff (Stage-0 column required)")
    ap.add_argument("--corpus", default=str(Path(__file__).parent.parent / "tests" / "flip" / "corpus"))
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "tests" / "flip" / "authoritative_findings.json"))
    args = ap.parse_args()

    corpus = sorted(Path(args.corpus).glob("*.parquet"))
    if not corpus:
        raise SystemExit(f"no corpus at {args.corpus} -- run scripts/flip_corpus.py first")

    print(f"corpus: {len(corpus)} datasets\n")
    all_p: dict[str, dict] = {}
    families_seen: set[str] = set()
    for path in corpus:
        name = path.stem
        findings, prof = run_authoritative(path)
        summ = _summarize(name, findings)
        families_seen.update(summ["by_check"])
        all_p[name] = {
            "summary": summ,
            "inferred_types": prof["columns"],
            "findings": [_finding_full(f) for f in findings],
        }
        checks = ", ".join(f"{k}:{v}" for k, v in sorted(summ["by_check"].items()))
        print(f"  {name:18s} {summ['n_findings']:4d} findings  [{checks}]")

    Path(args.out).write_text(json.dumps(all_p, indent=2, default=str))
    print(f"\nauthoritative findings -> {args.out}")
    print(f"check families triggered ({len(families_seen)}): {', '.join(sorted(families_seen))}")

    if not args.fused:
        print("\n(--fused not set: authoritative-only smoke capture. Fused side + diff pending Stage 0.)")
        return

    # --- Fused side + §8b differential (Stage 0) -----------------------------
    report_path = Path(__file__).parent.parent / "docs" / "superpowers" / "specs" / "flip-differential-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# Flip §8b Differential — Stage 0 (seam-clean column profilers)",
        "",
        "Authoritative = seam profilers over a `PolarsFrame` (2.x-Polars).  ",
        "Fused = the SAME profilers over an `ArrowFrame` (owned Arrow-native seam).",
        "",
        "Profilers exercised: TypeInference, Nullability, Uniqueness, RangeDistribution, "
        "Cardinality, SequenceDetection, Freshness.",
        "",
    ]

    overall_strict_inter = 0
    overall_strict_union = 0
    overall_dtype_diffs = 0
    overall_max_stat_delta = 0.0
    overall_strict_diffs: list[str] = []

    for path in corpus:
        name = path.stem
        p_find, p_neutral, p_repr = run_authoritative_seam(path)
        f_find, f_neutral, f_repr = run_fused_seam(path)

        # (1) full finding-set Jaccard (identity + sorted sample_values)
        p_full = {_full_key(f) for f in p_find}
        f_full = {_full_key(f) for f in f_find}
        full_inter = len(p_full & f_full)
        full_union = len(p_full | f_full) or 1
        full_jaccard = full_inter / full_union

        # count delta per (check, severity)
        p_cs = _count_by_check_sev(p_find)
        f_cs = _count_by_check_sev(f_find)
        cs_rows = []
        for key in sorted(set(p_cs) | set(f_cs)):
            pc_, fc_ = p_cs.get(key, 0), f_cs.get(key, 0)
            cs_rows.append((key[0], key[1], pc_, fc_, fc_ - pc_))

        # (2) dtype-vocab diff: raw-polars repr vs neutral (expected to differ)
        dtype_rows = []
        for col in p_repr:
            if p_repr[col] != f_repr.get(col):
                dtype_rows.append((col, p_repr[col], f_repr.get(col)))
        overall_dtype_diffs += len(dtype_rows)

        # (3) max stat-float delta per numeric check (parse message numbers)
        max_stat_delta, stat_detail = _max_stat_float_delta(p_find, f_find)
        overall_max_stat_delta = max(overall_max_stat_delta, max_stat_delta)

        # (4) STRICT set: non-sample, non-dtype findings that differ (must be empty)
        p_strict = {_strict_key(f) for f in p_find}
        f_strict = {_strict_key(f) for f in f_find}
        strict_inter = len(p_strict & f_strict)
        strict_union = len(p_strict | f_strict) or 1
        strict_jaccard = strict_inter / strict_union
        overall_strict_inter += strict_inter
        overall_strict_union += len(p_strict | f_strict)
        for k in sorted(p_strict ^ f_strict):
            side = "P-only" if k in p_strict else "F-only"
            overall_strict_diffs.append(f"{name}: {side} {k}")

        print(
            f"  {name:18s} full-J={full_jaccard:.3f}  strict-J={strict_jaccard:.3f}  "
            f"dtype-vocab-diffs={len(dtype_rows)}  max-stat-delta={max_stat_delta:.2e}"
        )

        lines += [
            f"## {name}",
            "",
            f"- full finding-set Jaccard (with sample_values): **{full_jaccard:.3f}** "
            f"({full_inter}/{full_union})",
            f"- STRICT (non-sample/non-dtype) Jaccard: **{strict_jaccard:.3f}** "
            f"({strict_inter}/{strict_union})",
            f"- max stat-float delta: **{max_stat_delta:.3e}**"
            + (f" ({stat_detail})" if stat_detail else ""),
            "",
            "### finding count per (check, severity)",
            "",
            "| check | severity | authoritative | fused | delta |",
            "|---|---|---|---|---|",
        ]
        for check, sev, pc_, fc_, delta in cs_rows:
            lines.append(f"| {check} | {sev} | {pc_} | {fc_} | {delta:+d} |")
        lines += ["", "### dtype vocabulary (expected raw-polars vs neutral divergence)", ""]
        if dtype_rows:
            lines += ["| column | authoritative repr | fused repr |", "|---|---|---|"]
            lines += [f"| {c} | `{a}` | `{b}` |" for c, a, b in dtype_rows]
        else:
            lines.append("_(no dtype_repr divergence)_")
        if p_strict ^ f_strict:
            lines += ["", "### STRICT-SET DIVERGENCE (kernel bug — blocks the Flip)", ""]
            for k in sorted(p_strict ^ f_strict):
                side = "P-only" if k in p_strict else "F-only"
                lines.append(f"- `{side}` {k}")
        lines.append("")

    overall_strict_j = overall_strict_inter / (overall_strict_union or 1)
    verdict = (
        f"STRICT (non-stat/non-dtype) Jaccard = {overall_strict_j:.3f} "
        f"(PASS iff 1.000)  ->  {'PASS' if overall_strict_j == 1.0 else 'FAIL'}"
    )
    summary = [
        "## Overall verdict",
        "",
        f"- {verdict}",
        f"- dtype-vocab diffs (expected, raw-polars vs neutral): **{overall_dtype_diffs}**",
        f"- max stat-float delta across all datasets: **{overall_max_stat_delta:.3e}**",
        f"- strict-set divergences: **{len(overall_strict_diffs)}** (must be 0)",
        "",
    ]
    if overall_strict_diffs:
        summary += ["### strict divergences", ""] + [f"- {d}" for d in overall_strict_diffs] + [""]
    lines = lines[:6] + summary + lines[6:]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + verdict)
    print(f"dtype-vocab diffs: {overall_dtype_diffs} | max stat-float delta: {overall_max_stat_delta:.3e}")
    print(f"strict-set divergences: {len(overall_strict_diffs)}")
    print(f"report -> {report_path}")


def _full_key(f) -> tuple:
    return (f.check, f.column, int(f.severity), f.affected_rows,
            tuple(sorted(str(v) for v in (getattr(f, "sample_values", None) or []))))


def _strict_key(f) -> tuple:
    """Identity WITHOUT sample_values — the strict-identity set (§8b metric 4)."""
    return (f.check, f.column, int(f.severity), f.affected_rows)


def _count_by_check_sev(findings: list) -> dict:
    out: dict = {}
    for f in findings:
        key = (f.check, int(f.severity))
        out[key] = out.get(key, 0) + 1
    return out


def _max_stat_float_delta(p_find: list, f_find: list) -> tuple[float, str]:
    """Max abs delta between the floats parsed from messages of findings matched
    by (check, column, severity). Catches stat drift the strict key would miss."""
    def by_key(fs):
        d: dict = {}
        for f in fs:
            d.setdefault((f.check, f.column, int(f.severity)), []).append(f)
        return d

    p_by, f_by = by_key(p_find), by_key(f_find)
    max_delta = 0.0
    detail = ""
    for key in set(p_by) & set(f_by):
        for pf, ff in zip(p_by[key], f_by[key]):
            pv = [float(x) for x in _FLOAT_RE.findall(pf.message)]
            fv = [float(x) for x in _FLOAT_RE.findall(ff.message)]
            for a, b in zip(pv, fv):
                delta = abs(a - b)
                if delta > max_delta:
                    max_delta = delta
                    detail = f"{key[0]}/{key[1]}"
    return max_delta, detail


if __name__ == "__main__":
    main()
