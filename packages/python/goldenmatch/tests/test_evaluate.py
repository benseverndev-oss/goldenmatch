"""Tests for evaluation engine."""
from __future__ import annotations

import textwrap

import polars as pl
import pytest
from goldenmatch.core.evaluate import EvalResult, evaluate_clusters, evaluate_pairs
from typer.testing import CliRunner


class TestEvaluatePairs:
    def test_perfect_pairs(self):
        """All predicted pairs are in ground truth."""
        predicted = [(1, 2, 0.9), (3, 4, 0.85)]
        ground_truth = {(1, 2), (3, 4)}
        result = evaluate_pairs(predicted, ground_truth)
        assert result.precision == 1.0
        assert result.recall == 1.0
        assert result.f1 == 1.0

    def test_partial_match(self):
        predicted = [(1, 2, 0.9), (5, 6, 0.8)]  # (5,6) is FP
        ground_truth = {(1, 2), (3, 4)}  # (3,4) is FN
        result = evaluate_pairs(predicted, ground_truth)
        assert result.tp == 1
        assert result.fp == 1
        assert result.fn == 1
        assert result.precision == 0.5
        assert result.recall == 0.5

    def test_empty_predicted(self):
        result = evaluate_pairs([], {(1, 2)})
        assert result.precision == 0.0
        assert result.recall == 0.0
        assert result.f1 == 0.0
        assert result.fn == 1

    def test_symmetric_pairs(self):
        """(1,2) should match (2,1) in ground truth."""
        predicted = [(2, 1, 0.9)]
        ground_truth = {(1, 2)}
        result = evaluate_pairs(predicted, ground_truth)
        assert result.tp == 1

    def test_empty_ground_truth(self):
        result = evaluate_pairs([(1, 2, 0.9)], set())
        assert result.precision == 0.0
        assert result.recall == 0.0


class TestEvaluateClusters:
    def test_cluster_to_pairs(self):
        """Clusters with >1 member generate pairs for evaluation."""
        clusters = {
            1: {"members": [1, 2, 3], "size": 3},
            2: {"members": [4], "size": 1},
        }
        ground_truth = {(1, 2), (1, 3), (2, 3)}
        result = evaluate_clusters(clusters, ground_truth)
        assert result.tp == 3
        assert result.precision == 1.0
        assert result.recall == 1.0


class TestEvalResult:
    def test_summary_dict(self):
        result = EvalResult(tp=8, fp=2, fn=1)
        d = result.summary()
        assert d["precision"] == pytest.approx(0.8, abs=1e-3)
        assert d["recall"] == pytest.approx(8 / 9, abs=1e-3)
        assert "f1" in d


# ── CLI tests ────────────────────────────────────────────────────────────

from goldenmatch.cli.main import app

runner = CliRunner()


class TestEvaluateCLI:
    @pytest.fixture
    def sample_data(self, tmp_path):
        # Create input CSV
        data_path = tmp_path / "data.csv"
        pl.DataFrame({
            "first_name": ["John", "john", "Jane", "Bob"],
            "last_name": ["Smith", "Smith", "Doe", "Jones"],
            "email": ["j@x.com", "j@x.com", "jane@t.com", "bob@t.com"],
        }).write_csv(data_path)

        # Create ground truth CSV (row 0 and 1 are dupes)
        gt_path = tmp_path / "ground_truth.csv"
        pl.DataFrame({"id_a": [0], "id_b": [1]}).write_csv(gt_path)

        # Create config
        config_path = tmp_path / "config.yaml"
        config_path.write_text(textwrap.dedent("""\
            matchkeys:
              - name: exact_email
                type: exact
                fields:
                  - field: email
                    transforms: [lowercase, strip]
        """))
        return data_path, gt_path, config_path

    def test_evaluate_basic(self, sample_data):
        data_path, gt_path, config_path = sample_data
        result = runner.invoke(app, [
            "evaluate",
            str(data_path),
            "--config", str(config_path),
            "--ground-truth", str(gt_path),
        ])
        assert result.exit_code == 0
        assert "Precision" in result.stdout or "precision" in result.stdout.lower()
        assert "Recall" in result.stdout or "recall" in result.stdout.lower()

    def test_evaluate_missing_gt(self, sample_data, tmp_path):
        data_path, _, config_path = sample_data
        result = runner.invoke(app, [
            "evaluate",
            str(data_path),
            "--config", str(config_path),
            "--ground-truth", str(tmp_path / "does_not_exist.csv"),
        ])
        assert result.exit_code != 0


# ── Threshold sweep & FS accuracy analysis (Phase 4) ───────────────────────


class TestThresholdSweep:
    def test_sweep_monotone_recall(self):
        from goldenmatch.core.evaluate import threshold_sweep
        # 3 true, 2 false; scores separate them.
        pairs = [(1, 2, 0.95), (3, 4, 0.90), (5, 6, 0.60), (7, 8, 0.40), (9, 10, 0.20)]
        gt = {(1, 2), (3, 4), (5, 6)}
        rows = threshold_sweep(pairs, gt, thresholds=[0.1, 0.5, 0.7, 0.92, 0.99])
        # Ascending threshold; recall non-increasing as threshold rises.
        ts = [r["threshold"] for r in rows]
        assert ts == sorted(ts)
        recalls = [r["recall"] for r in rows]
        assert recalls == sorted(recalls, reverse=True)
        # At t=0.5: predicts (1,2),(3,4),(5,6) -> all 3 true, R=1.0 P=1.0
        row_05 = next(r for r in rows if r["threshold"] == 0.5)
        assert row_05["tp"] == 3 and row_05["fp"] == 0
        assert row_05["precision"] == 1.0 and row_05["recall"] == 1.0

    def test_sweep_matches_evaluate_pairs(self):
        # A sweep row must equal evaluate_pairs at that threshold.
        from goldenmatch.core.evaluate import evaluate_pairs, threshold_sweep
        pairs = [(1, 2, 0.9), (3, 4, 0.8), (5, 6, 0.3)]
        gt = {(1, 2), (5, 6)}
        rows = threshold_sweep(pairs, gt, thresholds=[0.5])
        kept = [(a, b, s) for a, b, s in pairs if s >= 0.5]
        ref = evaluate_pairs(kept, gt)
        row = rows[0]
        assert (row["tp"], row["fp"], row["fn"]) == (ref.tp, ref.fp, ref.fn)
        assert row["precision"] == round(ref.precision, 4)

    def test_recommend_picks_max_f1(self):
        from goldenmatch.core.evaluate import recommend_threshold
        pairs = [(1, 2, 0.95), (3, 4, 0.90), (5, 6, 0.60), (7, 8, 0.55)]
        gt = {(1, 2), (3, 4), (5, 6)}
        rec = recommend_threshold(pairs, gt, thresholds=[0.5, 0.7, 0.92])
        # Best F1 is at a cut that keeps the 3 true and drops the false (7,8).
        assert rec["f1"] == max(r["f1"] for r in rec["sweep"])
        assert 0.5 <= rec["threshold"] <= 0.92

    def test_recommend_empty_safe(self):
        from goldenmatch.core.evaluate import recommend_threshold
        rec = recommend_threshold([], {(1, 2), (3, 4)})
        assert rec["f1"] == 0.0
        assert rec["fn"] == 2
        assert rec["sweep"] == []

    def test_default_thresholds_include_prob_anchors(self):
        from goldenmatch.core.evaluate import _default_thresholds
        ts = _default_thresholds([0.1, 0.5, 0.9, 0.99, 1.0])
        # Standard probability anchors within range are present.
        for a in (0.5, 0.9, 0.95, 0.99):
            assert a in ts


def _fs_model():
    import polars as pl
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import train_em
    df = pl.DataFrame({
        "__row_id__": list(range(8)),
        "name": ["a", "a", "bb", "bb", "ccc", "ccc", "dddd", "dddd"],
        "zip": ["1", "1", "2", "2", "3", "3", "4", "4"],
    })
    mk = MatchkeyConfig(name="fs", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
        MatchkeyField(field="zip", scorer="exact", levels=2),
    ])
    blocks = build_blocks(df.lazy(), BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]))
    em = train_em(df, mk, n_sample_pairs=100, blocks=blocks, blocking_fields=["zip"])
    return em, mk


class TestFSModelReport:
    def test_probability_two_random_records_match(self):
        from goldenmatch.core.evaluate import probability_two_random_records_match
        em, _mk = _fs_model()
        lam = probability_two_random_records_match(em)
        assert lam == em.proportion_matched
        assert 0.0 <= lam <= 1.0

    def test_model_report_shape(self):
        from goldenmatch.core.evaluate import fs_model_report
        em, mk = _fs_model()
        rep = fs_model_report(em, mk)
        assert set(rep) >= {"proportion_matched", "prior_bits", "converged", "iterations", "fields"}
        names = {f["field"] for f in rep["fields"]}
        assert names == {"name", "zip"}
        name_field = next(f for f in rep["fields"] if f["field"] == "name")
        assert name_field["n_levels"] == 3
        assert len(name_field["levels"]) == 3
        # Each level carries m, u, weight_bits keys.
        for lvl in name_field["levels"]:
            assert set(lvl) == {"level", "m", "u", "weight_bits"}


class TestThresholdSweepCLI:
    @pytest.fixture
    def fs_data(self, tmp_path):
        data_path = tmp_path / "data.csv"
        pl.DataFrame({
            "name": ["alexander", "alexandar", "bartholomew", "bartholomew",
                     "wilhelmina", "wilhelmina", "zachariah", "zacharia"],
            "zip": ["111", "111", "222", "222", "333", "333", "444", "444"],
        }).write_csv(data_path)
        # True dup pairs: (0,1),(2,3),(4,5),(6,7)
        gt_path = tmp_path / "gt.csv"
        pl.DataFrame({"id_a": [0, 2, 4, 6], "id_b": [1, 3, 5, 7]}).write_csv(gt_path)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(textwrap.dedent("""\
            blocking:
              keys:
                - fields: [zip]
            matchkeys:
              - name: fs
                type: probabilistic
                fields:
                  - field: name
                    scorer: jaro_winkler
                    levels: 3
                    partial_threshold: 0.8
                  - field: zip
                    scorer: exact
                    levels: 2
        """))
        return data_path, gt_path, config_path

    def test_threshold_sweep_emits_table_and_recommended_cut(self, fs_data):
        data_path, gt_path, config_path = fs_data
        result = runner.invoke(app, [
            "evaluate", str(data_path),
            "--config", str(config_path),
            "--ground-truth", str(gt_path),
            "--threshold-sweep",
        ])
        assert result.exit_code == 0, result.stdout
        out = result.stdout
        assert "Threshold sweep" in out
        assert "Recommended cut" in out
        # FS model report (m/u match weights) surfaces for the probabilistic mk.
        assert "Fellegi-Sunter model" in out
        assert "random match" in out  # probability_two_random_records_match

    def test_threshold_sweep_json_output(self, fs_data, tmp_path):
        data_path, gt_path, config_path = fs_data
        out_path = tmp_path / "out.json"
        result = runner.invoke(app, [
            "evaluate", str(data_path),
            "--config", str(config_path),
            "--ground-truth", str(gt_path),
            "--threshold-sweep",
            "--output", str(out_path),
        ])
        assert result.exit_code == 0, result.stdout
        import json
        payload = json.loads(out_path.read_text())
        assert "threshold_sweep" in payload
        ts = payload["threshold_sweep"]
        assert "recommended" in ts and "sweep" in ts
        assert "fs_model" in ts and "fs" in ts["fs_model"]
