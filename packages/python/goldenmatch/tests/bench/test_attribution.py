import importlib.util
from pathlib import Path

import pytest

# parents: [bench, tests, goldenmatch, python, packages, <repo-root>] -> [5] = repo root
REPO = Path(__file__).resolve().parents[5]
SPEC = REPO / "scripts" / "bench_er_headtohead" / "attribution.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_attribution", SPEC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.benchmark
def test_attribution_known_split():
    mod = _load()
    # 10 ground-truth matching pairs (ids 0..9 paired 0-1, 2-3, ... 18-19)
    gt = {(2 * i, 2 * i + 1) for i in range(10)}
    # candidate generation surfaced 7 of them (3 never blocked together)
    candidates = {(2 * i, 2 * i + 1) for i in range(7)} | {(0, 4), (1, 5)}  # +2 non-GT cands
    # scorer emitted 5 of the candidate GT pairs (2 scored but below threshold)
    emitted = {(2 * i, 2 * i + 1) for i in range(5)} | {(0, 4)}             # +1 non-GT emit
    rep = mod.attribution(gt_pairs=gt, candidate_pairs=candidates, emitted_pairs=emitted)
    assert rep["n_gt_pairs"] == 10
    assert rep["blocking_recall"] == 0.7      # 7/10 GT pairs survived blocking
    assert rep["final_recall"] == 0.5         # 5/10 emitted
    assert round(rep["threshold_loss"], 4) == 0.2  # (7-5)/10 GT lost at scoring
