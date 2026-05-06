"""GET /api/v1/runs/{name}/evaluation — F1/precision/recall vs steward labels.

The user's labels (web/labels.py + MemoryStore) are the closest thing the
workbench has to ground truth. This route turns them into a measurable
signal: pairs labeled `match` are positives; pairs labeled `non_match` are
negatives. Run the cluster output through ``evaluate_pairs`` against that
derived ground truth and surface the standard metrics.

Caveat that matters: ``evaluate_pairs`` computes recall as
``tp / (tp + fn)`` where ``fn`` = ``|ground_truth| - tp``. If the steward
has only labeled positives so far (no non_match labels), fp is whatever
the engine predicted that wasn't labeled a positive — which is not the
same as "wrong" and will tank precision. The response includes a
``label_counts`` block so the UI can warn when the label set is one-sided.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from goldenmatch.core.evaluate import evaluate_pairs
from goldenmatch.web import runs as runs_mod
from goldenmatch.web.labels import read_labels_dedup

router = APIRouter(prefix="/api/v1/runs")


def _find_run(state, run_name: str):
    for ref in runs_mod.discover_runs(state.runs_dir or state.project_root):
        if ref.run_name == run_name:
            return ref
    if state.registry is not None:
        ref = state.registry.get(run_name)
        if ref is not None:
            return ref
    raise HTTPException(status_code=404, detail=f"run not found: {run_name}")


@router.get("/{run_name}/evaluation")
def run_evaluation(run_name: str, request: Request) -> dict:
    state = request.app.state.app_state
    ref = _find_run(state, run_name)

    # Predicted pairs: every pair the run's lineage emitted with its score.
    lineage = runs_mod.load_lineage(ref)
    predicted: list[tuple[int, int, float]] = []
    for p in lineage.get("pairs", []):
        predicted.append((int(p["row_id_a"]), int(p["row_id_b"]), float(p["score"])))

    # Ground truth: positive labels become "should-match" pairs. Negative
    # labels (non_match) DON'T extend ground truth — they shrink the
    # "wrong predictions" set we'd otherwise call FPs. The downstream
    # UI uses both to render the band-of-confusion.
    labels = read_labels_dedup(state.labels_path)
    positives: set[tuple[int, int]] = set()
    negatives: set[tuple[int, int]] = set()
    for L in labels:
        a, b = int(L["row_id_a"]), int(L["row_id_b"])
        key = (a, b) if a <= b else (b, a)
        if L["label"] == "match":
            positives.add(key)
        else:
            negatives.add(key)

    result = evaluate_pairs(predicted, positives)

    # Surface the actual TP / FP / FN pair sets so the UI can render them
    # with the same field-level diff used in the cluster drilldown.
    pair_lookup = {
        ((a, b) if a <= b else (b, a)): p
        for p, (a, b) in (
            (p, (int(p["row_id_a"]), int(p["row_id_b"])))
            for p in lineage.get("pairs", [])
        )
    }
    pred_keys = set(pair_lookup.keys())

    tp_keys = pred_keys & positives
    # FP = predicted but not in positives. Filter further: if the user
    # labeled a pair as non_match, surface that explicitly (it's
    # confirmed-wrong) rather than mixing it into the unlabeled FP bucket.
    fp_keys_unlabeled = (pred_keys - positives) - negatives
    fp_keys_confirmed = (pred_keys - positives) & negatives
    # FN = positive in ground truth but not predicted.
    fn_keys = positives - pred_keys

    def _serialize(keys: set[tuple[int, int]]) -> list[dict]:
        out = []
        for k in keys:
            p = pair_lookup.get(k)
            if p is None:
                # Ground-truth-only pair — no lineage record. Render a stub
                # so the UI can still surface it.
                out.append({
                    "row_id_a": k[0],
                    "row_id_b": k[1],
                    "score": None,
                    "fields": [],
                    "cluster_id": None,
                })
            else:
                out.append(p)
        out.sort(key=lambda r: (r.get("score") or 0.0), reverse=True)
        return out

    summary = result.summary()
    summary["label_counts"] = {
        "positives": len(positives),
        "negatives": len(negatives),
        "total": len(positives) + len(negatives),
    }
    summary["confirmed_fp"] = len(fp_keys_confirmed)
    summary["unlabeled_fp"] = len(fp_keys_unlabeled)

    return {
        "summary": summary,
        "tp": _serialize(tp_keys),
        "fp_confirmed": _serialize(fp_keys_confirmed),
        "fp_unlabeled": _serialize(fp_keys_unlabeled),
        "fn": _serialize(fn_keys),
    }
