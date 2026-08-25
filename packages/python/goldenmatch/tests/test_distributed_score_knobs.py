"""#957: the four distributed score-tuning knobs must be readable AFTER import.

They were import-time module constants, which made them unreachable on the one
path that needs them. `ray submit` hands the driver a fresh shell, so nothing
exported around the submit survives; and the bench driver imports
`goldenmatch.distributed.*` before it sets any env of its own. The result was a
cluster bench that could not vary its own independent variable -- every sweep
silently ran the defaults, which looks exactly like a sweep that found no effect.

These tests import the module FIRST and set the environment SECOND, which is the
ordering the bug made impossible. They need no Ray: the accessors are plain env
reads, so the regression is guarded in ordinary CI rather than only on a cluster.
"""
from __future__ import annotations

import goldenmatch.distributed.scoring as S  # imported before any env is set


def test_num_cpus_reads_env_after_import(monkeypatch):
    assert S._score_num_cpus() == 2  # documented default
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS", "7")
    assert S._score_num_cpus() == 7


def test_project_reads_env_after_import(monkeypatch):
    assert S._score_project() is True  # default-on (#957 projection)
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_PROJECT", "0")
    assert S._score_project() is False


def test_concurrency_reads_env_after_import(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_SCORE_CONCURRENCY", raising=False)
    assert S._score_concurrency() is None  # unset = let Ray decide
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_CONCURRENCY", "48")
    assert S._score_concurrency() == 48


def test_op_reservation_reads_env_after_import(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_OP_RESERVATION", raising=False)
    assert S._op_reservation() is None
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_OP_RESERVATION", "0.2")
    assert S._op_reservation() == "0.2"


def test_effective_score_knobs_reports_what_the_run_will_use(monkeypatch):
    """The bench artifact's provenance record must track the live env.

    A sweep that reports no per-run configuration cannot be told apart from a
    sweep that never varied anything -- so this record is what makes a null
    result on the cluster believable.
    """
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS", "4")
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_PROJECT", "0")
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SCORE_CONCURRENCY", "60")
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_OP_RESERVATION", "0.2")
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_SHUFFLE_PARTS", "512")

    assert S.effective_score_knobs() == {
        "score_num_cpus": 4,
        "score_project": False,
        "score_concurrency": 60,
        "op_reservation": "0.2",
        "shuffle_parts": "512",
    }
