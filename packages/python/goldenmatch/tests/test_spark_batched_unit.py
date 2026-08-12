"""J1 unit tests: the batching contract, without Spark.

Runs on every PR. What can be checked here is narrow -- the module builds Spark
expressions, and expressions need Spark to mean anything -- so this pins the
parts that are pure decisions, and the source itself for the two constructions
whose absence would be a silent misalignment.

Reading source in a test is normally a smell. It earns its place here because
the alternative constructions do not fail: several `collect_list`s usually agree
on order, and two `explode`s usually interleave correctly. A test that only runs
the code would pass on the broken version, so the guard has to be structural.
"""
from __future__ import annotations

import inspect

import pytest
from goldenmatch.spark import batched


def test_only_the_partition_strategy_exists():
    with pytest.raises(ValueError, match="J4"):
        batched.batch_key("by_block")


def test_the_error_points_at_where_sizing_belongs():
    """Batch sizing is a throughput question; answering it without measurement
    is how a number gets baked in and never revisited."""
    with pytest.raises(ValueError) as err:
        batched.batch_key("nope")
    assert "measurement" in str(err.value)


# ── structural guards on the two silent-misalignment constructions ───

def test_exactly_one_collect_list_is_taken():
    """Several `collect_list`s in one aggregation are separate aggregate
    expressions with no shared order guarantee. They agree often enough to pass
    a test and skew under a different plan, so the module must take ONE list of
    structs and derive the per-field arrays from it.
    """
    src = inspect.getsource(batched.score_pairs_batched)
    # Count CALLS, not the word: the surrounding comment explains the trap
    # and mentions it several times.
    assert src.count("F.collect_list(") == 1, (
        "more than one collect_list: the per-field arrays can disagree on order "
        "and scores will attach to the wrong pairs"
    )
    assert "F.struct(" in src, "the single collect_list must collect a struct"
    assert "F.transform(" in src, (
        "per-field arrays must be DERIVED from the collected list, not collected "
        "separately"
    )


def test_arrays_are_zipped_before_exploding():
    """Two `explode`s over two arrays are two independent generators; nothing
    pairs element i of one with element i of the other. `arrays_zip` pairs them
    positionally first, so a single generator walks already-paired structs."""
    src = inspect.getsource(batched.score_pairs_batched)
    assert "arrays_zip" in src, "scores must be zipped to their rows before explode"
    assert src.count("F.explode(") == 1, (
        "more than one explode: independent generators do not align"
    )


def test_dedup_is_separable_from_scoring():
    """Kept apart so the two paths can be compared BEFORE dedup as well as
    after -- a misalignment that dedup happens to mask would otherwise be
    invisible."""
    assert callable(batched.dedup_max)
    assert "collect_list" not in inspect.getsource(batched.dedup_max)


def test_the_module_carries_no_scoring_of_its_own():
    """J1 is plan surgery. If scoring leaked in here, a misaligned result in J2
    could be blamed on the kernel instead of the plan -- which is the entire
    reason J1 exists before J2."""
    src = inspect.getsource(batched)
    for forbidden in ("jaro", "levenshtein", "token_sort", "strsim"):
        assert forbidden not in src.lower(), (
            f"{forbidden!r} appears in the batching module; scoring belongs to "
            f"the kernel, not to the plan reshape"
        )
