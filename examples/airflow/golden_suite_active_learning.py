"""Active learning — turn steward decisions into a fresh classifier.

Pairs with `golden_suite_review_worker`. Once you have N labeled pairs from
human review, this DAG retrains GoldenMatch's boost classifier on those
labels, evaluates the new model on a held-out slice, and — if it beats the
current model — saves a new config snapshot that downstream DAGs can pick up.

The "pick up" handoff is intentionally explicit: the new config is written to
a versioned S3 path, and the daily / customer-360 DAGs read a `current.yaml`
pointer that this DAG only updates when the new model is strictly better.
That keeps a bad retrain from silently degrading production.

Requires:
    pip install apache-airflow goldenmatch[memory,llm] scikit-learn \\
                apache-airflow-providers-amazon apache-airflow-providers-postgres
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable

REVIEW_QUEUE_TABLE = "warehouse.customers_review_queue"

# Need this many decided pairs before retraining is worthwhile.
MIN_LABELED_PAIRS = 200

# Config snapshot output paths
CONFIG_SNAPSHOT_PREFIX = "configs/goldenmatch/"
CURRENT_CONFIG_KEY = "configs/goldenmatch/current.yaml"


@dag(
    dag_id="golden_suite_active_learning",
    description="Retrain match boost classifier from review-queue labels. Promote only if better.",
    schedule="0 3 * * 0",  # Sunday 03:00 UTC, weekly
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 1, "retry_delay": timedelta(minutes=10)},
    tags=["golden-suite", "active-learning", "ml"],
)
def golden_suite_active_learning():
    """Retrain and conditionally promote a new GoldenMatch boost model."""

    @task
    def collect_labeled_pairs() -> list[dict]:
        """Pull resolved pairs from the review queue. Each row already has a decision."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook

        rows = PostgresHook(postgres_conn_id="postgres_default").get_records(
            f"""
            SELECT payload, candidate_id, score, decision
            FROM {REVIEW_QUEUE_TABLE}
            WHERE decided_at IS NOT NULL AND decision IN ('approve', 'reject')
            ORDER BY decided_at DESC
            LIMIT 5000
            """
        )
        return [
            {"payload": r[0], "candidate_id": r[1], "score": r[2], "decision": r[3]}
            for r in rows
        ]

    @task
    def gate_min_labels(pairs: list[dict]) -> list[dict]:
        """Skip retraining if we don't have enough labels. Caller treats empty as no-op."""
        if len(pairs) < MIN_LABELED_PAIRS:
            import logging
            logging.info(
                "Active learning skipped: only %d labeled pairs, need %d.",
                len(pairs), MIN_LABELED_PAIRS,
            )
            return []
        return pairs

    @task
    def train_and_evaluate(pairs: list[dict]) -> dict[str, Any]:
        """Train a new boost model on 80% of labels, evaluate on remaining 20%."""
        if not pairs:
            return {"trained": False}

        import json
        import random

        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import f1_score, precision_score, recall_score

        random.seed(42)
        random.shuffle(pairs)
        split = int(len(pairs) * 0.8)
        train, test = pairs[:split], pairs[split:]

        def featurize(p: dict) -> list[float]:
            payload = p["payload"] if isinstance(p["payload"], dict) else json.loads(p["payload"])
            # Stand-in features: pair score plus a couple of payload-derived signals.
            # Replace with your real feature extractor.
            return [
                float(p["score"] or 0),
                len(str(payload.get("first_name", ""))),
                len(str(payload.get("last_name", ""))),
                int("@" in str(payload.get("email", ""))),
            ]

        def label(p: dict) -> int:
            return 1 if p["decision"] == "approve" else 0

        X_tr = np.array([featurize(p) for p in train])
        y_tr = np.array([label(p) for p in train])
        X_te = np.array([featurize(p) for p in test])
        y_te = np.array([label(p) for p in test])

        clf = LogisticRegression(max_iter=200).fit(X_tr, y_tr)
        y_pred = clf.predict(X_te)

        metrics = {
            "trained": True,
            "n_train": len(train),
            "n_test": len(test),
            "precision": float(precision_score(y_te, y_pred, zero_division=0)),
            "recall": float(recall_score(y_te, y_pred, zero_division=0)),
            "f1": float(f1_score(y_te, y_pred, zero_division=0)),
            # Weights are the only thing the GoldenMatch config snapshot really needs.
            "coefficients": clf.coef_[0].tolist(),
            "intercept": float(clf.intercept_[0]),
        }
        return metrics

    @task
    def compare_to_current(metrics: dict[str, Any]) -> dict[str, Any]:
        """Read current model's logged metrics; promote only if F1 beats it."""
        if not metrics.get("trained"):
            return {"promote": False, **metrics}

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import yaml

        s3 = S3Hook(aws_conn_id="aws_default")
        try:
            current_yaml = s3.read_key(CURRENT_CONFIG_KEY,
                                       bucket_name=Variable.get("golden_suite_bucket"))
            current_f1 = yaml.safe_load(current_yaml).get("training_metrics", {}).get("f1", 0.0)
        except Exception:  # noqa: BLE001
            current_f1 = 0.0

        # Require a meaningful improvement to avoid churn from noisy samples.
        promote = metrics["f1"] >= current_f1 + 0.01
        return {"promote": promote, "current_f1": current_f1, **metrics}

    @task
    def write_snapshot(decision: dict[str, Any], **context) -> str | None:
        """Write a versioned config snapshot. Update the current.yaml pointer iff promote."""
        if not decision.get("trained"):
            return None

        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        import yaml

        run_id = context["run_id"]
        snapshot = {
            "version": run_id,
            "training_metrics": {
                "precision": decision["precision"],
                "recall": decision["recall"],
                "f1": decision["f1"],
                "n_train": decision["n_train"],
            },
            "boost": {
                "coefficients": decision["coefficients"],
                "intercept": decision["intercept"],
            },
        }
        snapshot_yaml = yaml.safe_dump(snapshot)
        s3 = S3Hook(aws_conn_id="aws_default")

        # Always write the versioned snapshot for traceability.
        snapshot_key = f"{CONFIG_SNAPSHOT_PREFIX}{run_id}.yaml"
        s3.load_string(snapshot_yaml,
                       key=snapshot_key,
                       bucket_name=Variable.get("golden_suite_bucket"),
                       replace=True)

        if decision.get("promote"):
            s3.load_string(snapshot_yaml,
                           key=CURRENT_CONFIG_KEY,
                           bucket_name=Variable.get("golden_suite_bucket"),
                           replace=True)
            import logging
            logging.info(
                "Promoted active-learning snapshot %s. F1: %s -> %s",
                run_id, decision.get("current_f1"), decision["f1"],
            )
        return snapshot_key

    pairs = collect_labeled_pairs()
    gated = gate_min_labels(pairs)
    metrics = train_and_evaluate(gated)
    decision = compare_to_current(metrics)
    write_snapshot(decision)


golden_suite_active_learning()
