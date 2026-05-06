"""GET /api/v1/runs/{name}/evaluation — F1/precision/recall vs steward labels."""
from __future__ import annotations


def test_evaluation_with_only_positive_label_finds_tp(client):
    # Pair (0, 1) is the only pair in the fixture run, with score 0.9.
    # Label it as a match → it's a TP, no FN, FP = 0 (only one predicted).
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "match"},
    )
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    s = body["summary"]
    assert s["tp"] == 1
    assert s["fp"] == 0
    assert s["fn"] == 0
    assert s["precision"] == 1.0
    assert s["recall"] == 1.0
    assert s["f1"] == 1.0
    assert s["label_counts"]["positives"] == 1
    assert s["label_counts"]["negatives"] == 0
    assert len(body["tp"]) == 1


def test_evaluation_unlabeled_predicted_pair_counts_as_unlabeled_fp(client):
    """If the engine predicts (0,1) but the user hasn't labeled it, that's
    an UNLABELED fp — distinct from a confirmed-wrong fp. The summary
    should keep them separate so the UI can render the band of confusion
    rather than treating every unlabeled prediction as a mistake."""
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    s = body["summary"]
    # No labels yet → all predicted pairs are unlabeled fp; tp is 0 because
    # there are no positives in ground truth.
    assert s["tp"] == 0
    assert s["unlabeled_fp"] == 1
    assert s["confirmed_fp"] == 0
    assert s["label_counts"]["total"] == 0


def test_evaluation_confirmed_fp_when_pair_labeled_non_match(client):
    """A pair labeled non_match that the engine still predicts is a
    confirmed FP — the user has explicitly told us this isn't a match."""
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 1, "label": "non_match"},
    )
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    s = body["summary"]
    assert s["tp"] == 0
    assert s["confirmed_fp"] == 1
    assert s["unlabeled_fp"] == 0
    assert s["label_counts"]["negatives"] == 1
    assert len(body["fp_confirmed"]) == 1


def test_evaluation_fn_when_positive_not_predicted(client):
    """If the user labels a pair as match but the engine didn't predict it,
    that pair is a FN."""
    client.post(
        "/api/v1/labels",
        json={"row_id_a": 0, "row_id_b": 2, "label": "match"},  # not in lineage
    )
    body = client.get("/api/v1/runs/20260101_000000/evaluation").json()
    s = body["summary"]
    assert s["tp"] == 0
    assert s["fn"] == 1
    assert s["recall"] == 0.0
    assert len(body["fn"]) == 1


def test_evaluation_404_on_unknown_run(client):
    assert client.get("/api/v1/runs/nope/evaluation").status_code == 404
