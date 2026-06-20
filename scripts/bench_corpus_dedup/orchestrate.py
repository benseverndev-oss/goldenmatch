#!/usr/bin/env python
"""Orchestrator for the corpus-dedup throughput benchmark (GoldenMatch tier vs datatrove).

Subprocess-per-(corpus, scale, engine): the OS reclaims each datapoint's memory on exit, an
OOM-killed datapoint (no JSON written) is recorded `status: OOM`, and the aggregate JSON is
flushed after every datapoint. Accuracy reuses the engine-agnostic head-to-head evaluator
(`bench_er_headtohead/evaluate.py`) by path — `record_id := doc_id`, identical parquet contract.

Output: `summary.md` (the docs/sec + MB/sec vs datatrove headline, recall alongside, NeMo cited)
+ `bench_results.json`. Usage:
    python orchestrate.py --corpus fineweb --scales 100000 1000000 \
        --engines goldenmatch datatrove --recall-target 0.95 --workdir .bench_corpus
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATOR = HERE.parent / "bench_er_headtohead" / "evaluate.py"

# NeMo-Curator GPU fuzzy-dedup reference (NOT run here — GPU/RAPIDS, not CI-runnable).
# Cited from NVIDIA's published NeMo-Curator fuzzy-dedup throughput; labelled GPU, not measured.
NEMO_REFERENCE = {
    "engine": "nemo-curator (cited, GPU, NOT run)",
    "note": "NVIDIA NeMo-Curator fuzzy dedup (MinHashLSH on GPU/RAPIDS). Published throughput "
            "is hardware-specific (multi-GPU); shown for reference only, not measured on this box.",
}


def _run(cmd: list[str], timeout: int) -> int:
    try:
        return subprocess.run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return -1


def _load_or_synthesize(path: Path, rc: int, engine: str) -> dict:
    if path.exists():
        try:
            r = json.loads(path.read_text())
            r.setdefault("returncode", rc)
            return r
        except Exception:
            pass
    status = "OOM" if rc in (-9, 137) else ("timeout" if rc == -1 else "killed")
    return {"engine": engine, "status": status, "returncode": rc,
            "note": "no result file — process terminated by OS (likely OOM) or timed out"}


def build_fixture(corpus: str, scale: int, seed: int, frac: float, fx_dir: Path) -> tuple[Path, Path]:
    fx_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(HERE / "inject_dups.py"), "--corpus", corpus,
         "--n-docs", str(scale), "--seed", str(seed), "--frac", str(frac),
         "--out-dir", str(fx_dir)],
        check=True,
    )
    return fx_dir / "corpus.parquet", fx_dir / "truth.parquet"


def run_engine(engine: str, corpus_pq: Path, results_dir: Path, label: str,
               recall_target: float, timeout: int) -> tuple[dict, Path]:
    out = results_dir / f"{label}_{engine}.json"
    pred = results_dir / f"{label}_{engine}.pred.parquet"
    for stale in (out, pred):
        stale.unlink(missing_ok=True)
    cmd = [sys.executable, str(HERE / f"run_{engine}.py"),
           "--input", str(corpus_pq), "--out", str(out), "--pred-out", str(pred)]
    if engine == "goldenmatch":
        cmd += ["--recall-target", str(recall_target)]
    rc = _run(cmd, timeout)
    return _load_or_synthesize(out, rc, engine), pred


def evaluate(pred: Path, truth: Path, results_dir: Path, label: str, engine: str, timeout: int) -> dict | None:
    if not pred.exists() or not truth.exists():
        return None
    metrics_out = results_dir / f"{label}_{engine}.metrics.json"
    _run([sys.executable, str(EVALUATOR), "--pred", str(pred), "--truth", str(truth),
          "--out", str(metrics_out)], timeout)
    if metrics_out.exists():
        try:
            return json.loads(metrics_out.read_text())
        except Exception:
            return None
    return None


def _f(v, nd=1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def render_markdown(results: list[dict], corpus: str) -> str:
    lines = [
        "# Corpus near-dup throughput: GoldenMatch throughput tier vs datatrove",
        "",
        f"Corpus: `{corpus}`. One machine, identical per-scale corpus (real text + injected "
        "ground-truth near-dups). Wall is the dedupe call; **docs/sec** and **MB/sec** are the "
        "headline throughput; **pairwise R** is the recall each engine achieved on the injected "
        "dups (throughput is only meaningful *at a stated recall*).",
        "",
        "| corpus | docs | engine | status | wall (s) | peak RSS (MB) | docs/sec | MB/sec | candidate pairs | pairwise R | pairwise F1 |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(results, key=lambda x: (x.get("n_docs") or 0, x.get("engine", ""))):
        acc = r.get("accuracy") or {}
        pw = acc.get("pairwise") or {}
        lines.append("| " + " | ".join([
            corpus, _f(r.get("n_docs")), r.get("engine", "?"), r.get("status", "?"),
            _f(r.get("dedupe_wall_seconds")), _f(r.get("peak_rss_mb")),
            _f(r.get("docs_per_sec")), _f(r.get("mb_per_sec"), 3), _f(r.get("candidate_pairs")),
            _f(pw.get("recall"), 3), _f(pw.get("f1"), 3),
        ]) + " |")

    # vs-datatrove throughput delta where both completed at the same scale.
    lines += ["", "## Throughput vs datatrove (where both completed)", "",
              "| docs | GM docs/sec | datatrove docs/sec | speedup (GM/dt) | GM recall | dt recall |",
              "|---:|---:|---:|---:|---:|---:|"]
    by_scale: dict[int, dict[str, dict]] = {}
    for r in results:
        by_scale.setdefault(r.get("n_docs") or 0, {})[r.get("engine", "?")] = r
    for n in sorted(by_scale):
        gm = by_scale[n].get("goldenmatch", {})
        dt = by_scale[n].get("datatrove", {})
        gd, dd = gm.get("docs_per_sec"), dt.get("docs_per_sec")
        speedup = round(gd / dd, 2) if (gd and dd) else None
        gr = ((gm.get("accuracy") or {}).get("pairwise") or {}).get("recall")
        dr = ((dt.get("accuracy") or {}).get("pairwise") or {}).get("recall")
        lines.append("| " + " | ".join([
            _f(n), _f(gd), _f(dd), _f(speedup, 2), _f(gr, 3), _f(dr, 3)]) + " |")

    lines += ["", "## Reference (not measured here)", "",
              f"- **{NEMO_REFERENCE['engine']}** — {NEMO_REFERENCE['note']}", ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="offline")
    ap.add_argument("--scales", type=int, nargs="+", default=[1000])
    ap.add_argument("--engines", nargs="+", default=["goldenmatch", "datatrove"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--frac", type=float, default=0.4)
    ap.add_argument("--recall-target", type=float, default=0.95)
    ap.add_argument("--workdir", type=Path, default=Path(".bench_corpus"))
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    fixtures = args.workdir / "fixtures"
    results_dir = args.workdir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for scale in args.scales:
        label = f"{args.corpus}_{scale}"
        fx_dir = fixtures / label
        try:
            corpus_pq, truth = build_fixture(args.corpus, scale, args.seed, args.frac, fx_dir)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            for engine in args.engines:
                all_results.append({"engine": engine, "n_docs": scale,
                                    "status": "fixture_failed", "error": str(e)})
            continue
        for engine in args.engines:
            print(f"[orchestrate] === {engine} @ {scale} docs ({args.corpus}) ===")
            t0 = time.perf_counter()
            res, pred = run_engine(engine, corpus_pq, results_dir, label,
                                   args.recall_target, args.timeout)
            res["orchestrator_wall_seconds"] = round(time.perf_counter() - t0, 1)
            acc = evaluate(pred, truth, results_dir, label, engine, args.timeout)
            if acc is not None:
                res["accuracy"] = acc
            pred.unlink(missing_ok=True)
            all_results.append(res)
            (args.workdir / "bench_results.json").write_text(json.dumps(all_results, indent=2))
        for f in fx_dir.glob("*.parquet"):
            f.unlink(missing_ok=True)

    md = render_markdown(all_results, args.corpus)
    (args.workdir / "summary.md").write_text(md)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(md)


if __name__ == "__main__":
    main()
