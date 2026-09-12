"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Where is F1 lost on the FS link-cut routing design corpus? Each dataset runs in its own child
process, so an OOM or crash loses one dataset, not the run. Three phases run per dataset:

  zero_config    dedupe_df(df) exactly as a user calls it. Reports cluster P/R/F1, the matchkey
                 types it chose, and the blocking ceiling of its config.
  deterministic  auto_configure_df under deterministic_routing(). Reports cluster P/R/F1 and the
                 blocking ceiling.
  fs             auto_configure_probabilistic_df with review_threshold=0.0 on every probabilistic
                 matchkey, so every scored pair below the link cut comes back in review_pairs
                 (review pairs never cluster). Reports cluster P/R/F1, the blocking ceiling,
                 pairwise P/R/F1 at the link cut, and the best pairwise F1 over every threshold.

Reading one row:
  blocking_recall low                          -> blocking loses true pairs before scoring
  best_pairwise_f1 low, blocking_recall high   -> scores do not separate matches (features / EM)
  best_pairwise_f1 well above pairwise at cut  -> the cut is misplaced
  pairwise at cut well above cluster f1        -> clustering (transitive merges) costs precision
  max(fs, deterministic) well above zero_config -> zero-config picked the weaker strategy

Usage, from the repo root:
  python -m scripts.spike.fs_loss_probe --out DIR [--datasets a,b]
  python -m scripts.spike.fs_loss_probe --child NAME --out DIR
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

CHILD_TIMEOUT_S = int(os.environ.get("FS_LOSS_CHILD_TIMEOUT_S", "5400"))
#: Review cut for the FS phase: every scored candidate above it comes back in review_pairs.
_REVIEW_FLOOR = 1e-6
#: Largest datasets last, so the small ones report early.
_LAST = ("musicbrainz_20k", "dblp_scholar", "historical_50k")


def _prf(tp: int, n_pred: int, n_gt: int) -> dict:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gt if n_gt else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "pairs": n_pred}


def _cluster(result, gt: set) -> dict:
    from goldenmatch.core.evaluate import evaluate_clusters

    s = evaluate_clusters(result.clusters, gt).summary()
    return {k: s[k] for k in ("precision", "recall", "f1")}


def _canon(pairs) -> dict:
    out: dict = {}
    for a, b, s in pairs:
        key = (min(a, b), max(a, b))
        if float(s) > out.get(key, -1.0):
            out[key] = float(s)
    return out


def _types(cfg) -> list[str]:
    return sorted({mk.type for mk in cfg.get_matchkeys()}) if cfg is not None else []


def _blocking_recall(df, cfg, gt: set) -> dict:
    """Share of true pairs that share at least one block under ``cfg.blocking``."""
    from goldenmatch.core.blocker import build_blocks

    blocking = getattr(cfg, "blocking", None)
    if blocking is None:
        return {"blocking_recall": 1.0, "blocking_note": "no blocking config"}
    frame = df.with_row_index("__row_id__")
    blocks_of: dict[int, set[int]] = {}
    projected = 0
    n_blocks = 0
    max_block = 0
    for bi, b in enumerate(build_blocks(frame, blocking)):
        col = b.df.column("__row_id__") if hasattr(b.df, "column") else b.df["__row_id__"]
        ids = col.to_pylist() if hasattr(col, "to_pylist") else col.to_list()
        projected += len(ids) * (len(ids) - 1) // 2
        n_blocks += 1
        max_block = max(max_block, len(ids))
        for rid in ids:
            blocks_of.setdefault(int(rid), set()).add(bi)
    covered = sum(1 for a, c in gt if blocks_of.get(a, set()) & blocks_of.get(c, set()))
    return {
        "blocking_recall": round(covered / len(gt), 4) if gt else None,
        "candidate_pairs": projected,
        "blocks": n_blocks,
        "max_block": max_block,
    }


def _zero_config(df, gt: set) -> dict:
    import goldenmatch

    result = goldenmatch.dedupe_df(df, confidence_required=False)
    return {
        **_cluster(result, gt),
        "matchkey_types": _types(result.config),
        **_blocking_recall(df, result.config, gt),
    }


def _deterministic(df, gt: set) -> dict:
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_df, deterministic_routing

    with deterministic_routing():
        cfg = auto_configure_df(df, confidence_required=False)
    result = goldenmatch.dedupe_df(df, config=cfg, confidence_required=False)
    return {**_cluster(result, gt), "matchkey_types": _types(cfg), **_blocking_recall(df, cfg, gt)}


def _fs(df, gt: set) -> dict:
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    cfg = auto_configure_probabilistic_df(df)
    mks = [
        mk.model_copy(update={"review_threshold": _REVIEW_FLOOR})
        if mk.type == "probabilistic"
        else mk
        for mk in cfg.get_matchkeys()
    ]
    cfg = cfg.model_copy(update={"matchkeys": mks, "match_settings": None})
    result = goldenmatch.dedupe_df(df, config=cfg, confidence_required=False)
    linked = _canon(result.scored_pairs or [])
    review = _canon(getattr(result, "review_pairs", None) or [])
    every = dict(review)
    every.update(linked)
    n_gt = len(gt)
    at_cut = _prf(len(linked.keys() & gt), len(linked), n_gt)
    # True pairs that received any score: below blocking recall when oversized blocks are skipped.
    reachable = round(len(every.keys() & gt) / n_gt, 4) if n_gt else None
    ranked = sorted(every.items(), key=lambda kv: -kv[1])
    tp = 0
    best: dict = {"f1": 0.0, "threshold": None}
    for i, (key, score) in enumerate(ranked, 1):
        if key in gt:
            tp += 1
        if i == len(ranked) or ranked[i][1] < score:
            m = _prf(tp, i, n_gt)
            if m["f1"] > best["f1"]:
                best = {**m, "threshold": round(score, 4)}
    thresholds = {
        mk: {
            k: entry.get(k) for k in ("link_threshold", "source", "cut_rule", "cut_reason", "refit")
        }
        for mk, entry in ((result.stats or {}).get("fs_link_thresholds") or {}).items()
    }
    return {
        **_cluster(result, gt),
        "matchkey_types": _types(cfg),
        **_blocking_recall(df, cfg, gt),
        "reachable_recall": reachable,
        "pairwise_at_cut": at_cut,
        "best_pairwise": best,
        "scored_pairs": len(linked),
        "review_pairs": len(review),
        "link_thresholds": thresholds,
    }


def _phase(record: dict, name: str, fn) -> None:
    t0 = time.perf_counter()
    try:
        record[name] = fn()
    except Exception as exc:  # a failed phase must not lose the other phases
        record[name] = {
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-3000:],
        }
    record[name]["seconds"] = round(time.perf_counter() - t0, 1)
    print(
        f"[fs-loss] {record['dataset']}:{name} {record[name].get('f1', record[name].get('error'))}",
        flush=True,
    )


def child(name: str, out: Path) -> None:
    from scripts.autoconfig_quality.rule_sweep import resolve_loader

    record: dict = {"dataset": name}
    t0 = time.perf_counter()
    target = out / f"{name}.json"
    partial = out / f"{name}.partial.json"

    def save(path: Path) -> None:
        record["seconds"] = round(time.perf_counter() - t0, 1)
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    loaded = resolve_loader(name)()
    if loaded is None or not loaded[1]:
        record["skipped"] = "unavailable or unlabelled"
    else:
        df, gt = loaded
        gt = {(min(a, b), max(a, b)) for a, b in gt}
        record.update(rows=df.height, gt_pairs=len(gt))
        # Saved after every phase, so an OOM kill in a later phase keeps the earlier ones.
        for phase, fn in (
            ("zero_config", _zero_config),
            ("deterministic", _deterministic),
            ("fs", _fs),
        ):
            _phase(record, phase, lambda fn=fn: fn(df, gt))
            save(partial)
    save(target)
    partial.unlink(missing_ok=True)


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def summarize(out: Path, names: list[str]) -> str:
    cols = [
        "dataset",
        "rows",
        "zero-config F1 (types)",
        "det F1",
        "FS F1",
        "FS blocking recall",
        "FS reachable recall",
        "FS best pairwise F1 @t",
        "FS pairwise F1 @cut",
        "FS cut rule",
        "zero-config blocking recall",
    ]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for name in names:
        p = out / f"{name}.json"
        r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"crashed": "missing"}
        if "crashed" in r or "skipped" in r:
            reason = r.get("crashed") or r.get("skipped")
            lines.append(f"| {name} | {reason} |" + " |" * (len(cols) - 2))
            continue

        def cell(phase: str, key: str):
            if _get(r, phase, "error"):
                return "error"
            v = _get(r, phase, key)
            return "-" if v is None else v

        types = ",".join(_get(r, "zero_config", "matchkey_types") or [])
        rules = ",".join(
            sorted(
                {str(e.get("cut_rule")) for e in (_get(r, "fs", "link_thresholds") or {}).values()}
            )
        )
        best = (
            f"{_get(r, 'fs', 'best_pairwise', 'f1')} @{_get(r, 'fs', 'best_pairwise', 'threshold')}"
        )
        lines.append(
            f"| {name} | {r.get('rows')} | {cell('zero_config', 'f1')} ({types}) | {cell('deterministic', 'f1')} "
            f"| {cell('fs', 'f1')} | {cell('fs', 'blocking_recall')} | {cell('fs', 'reachable_recall')} | {best} "
            f"| {_get(r, 'fs', 'pairwise_at_cut', 'f1')} | {rules} | {cell('zero_config', 'blocking_recall')} |"
        )
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


def driver(out: Path, names: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONHASHSEED": "0", "GOLDENMATCH_AUTOCONFIG_MEMORY": "0"}
    for name in names:
        target = out / f"{name}.json"
        if target.exists():
            print(f"[fs-loss] {name}: already measured, skipping", flush=True)
            continue
        print(f"[fs-loss] {name}: start", flush=True)
        t0 = time.perf_counter()
        argv = [
            sys.executable,
            "-m",
            "scripts.spike.fs_loss_probe",
            "--child",
            name,
            "--out",
            str(out),
        ]
        try:
            rc = subprocess.run(argv, env=env, timeout=CHILD_TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        if not target.exists():
            target.write_text(
                json.dumps({"dataset": name, "crashed": f"rc={rc}"}), encoding="utf-8"
            )
        print(f"[fs-loss] {name}: rc={rc} {time.perf_counter() - t0:.0f}s", flush=True)
    print(summarize(out, names), flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="THROWAWAY: per-dataset F1 loss decomposition.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--child", default=None)
    ap.add_argument("--datasets", default="")
    args = ap.parse_args(argv)
    if args.child:
        child(args.child, args.out)
        return 0
    from scripts.autoconfig_quality.corpus import ci_datasets

    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or ci_datasets("design")
    names = [n for n in names if n not in _LAST] + [n for n in _LAST if n in names]
    driver(args.out, names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
