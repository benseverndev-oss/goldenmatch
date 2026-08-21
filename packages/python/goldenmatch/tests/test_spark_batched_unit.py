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
    with pytest.raises(ValueError, match="only 'partition' exists"):
        batched.batch_key("by_block")


def test_the_batch_must_be_bounded():
    """The bug the bench found, pinned.

    J1 shipped with the batch keyed on `spark_partition_id()` alone -- one call
    per partition, described in its own docstring as "as large a batch as can be
    formed without a shuffle". True, and exactly the problem: groupBy +
    collect_list materialises the group as an array in JVM heap, so group size is
    a MEMORY COMMITMENT, not a throughput knob. 1.9M candidate pairs gave
    `java.lang.OutOfMemoryError: Java heap space` (bench run 31625487603).

    Sizing was deferred as "a J4 measurement question". It is a
    correctness-at-scale question, and a non-positive size is refused rather than
    treated as "unlimited".
    """
    for bad in (0, -1, -10_000):
        with pytest.raises(ValueError, match="must be positive"):
            batched.batch_key("partition", bad)


def test_the_refusal_explains_why_size_is_not_a_tuning_knob():
    """A reader who sees the error must learn WHY, or they will raise the number
    until it stops failing rather than understand what it costs."""
    with pytest.raises(ValueError) as err:
        batched.batch_key("partition", 0)
    msg = str(err.value)
    assert "memory" in msg and "array" in msg


def test_the_default_batch_size_is_bounded_and_probe_backed():
    """10,000 is not a guess: the Connect probe carried that many pairs in one
    call (run 31611464914). Unbounded or trivially small are both wrong -- one
    OOMs, the other reinstates the per-call overhead batching exists to remove.
    """
    assert 1_000 <= batched.DEFAULT_BATCH_SIZE <= 100_000


def test_score_pairs_batched_exposes_the_size():
    """A default that cannot be overridden is a constant with extra steps, and
    the right value depends on row width and executor heap."""
    import inspect

    sig = inspect.signature(batched.score_pairs_batched)
    assert "batch_size" in sig.parameters
    assert sig.parameters["batch_size"].default == batched.DEFAULT_BATCH_SIZE


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


# ── J4's only open lever: filter BEFORE the generator ────────────────

def test_the_threshold_filter_is_applied_before_the_explode():
    """The entire point of the parameter, and invisible to an output check.

    Filtering after the explode returns the SAME ROWS as filtering inside the
    array, so no behavioural test can tell the two apart -- and the difference
    is the whole reason the parameter exists. J4's plan bisect attributed the
    batched arm's 2.4x to `arrays_zip`/`explode` (~1.2s for 1.9M pairs, six
    times the ~0.2s of actual JNI scoring), so a filter that runs after the
    generator has already paid for every rejected pair buys nothing.

    Structural, therefore, in the same spirit as the two guards above.
    """
    src = inspect.getsource(batched.score_pairs_batched)
    assert "F.filter(" in src, "the threshold must filter the ARRAY"
    assert src.index("F.filter(") < src.index("F.explode("), (
        "the filter must come before the explode; after it, every rejected pair "
        "has already been materialised as a row and the filter saves nothing"
    )
    # A post-generator row filter would be the natural regression -- it reads
    # identically at the call site and passes every parity test.
    for after_the_fact in (".where(", ".filter(F.col"):
        assert after_the_fact not in src, (
            f"{after_the_fact!r} filters rows after the generator; filter the "
            f"array instead"
        )


def test_the_threshold_predicate_takes_ONE_parameter():
    """PySpark reads a higher-order lambda's meaning from its parameter count.

    One parameter is `(element)`; two is `(element, index)`. So
    `lambda z, t=threshold:` is read as an indexed callback and `t` receives the
    ELEMENT INDEX -- a filter comparing a score against a row number, with no
    error and no crash. This repo has already paid for that trap once, in the
    JVM scoring reshape.
    """
    import inspect as _inspect

    predicate = batched._above(0.5)
    assert len(_inspect.signature(predicate).parameters) == 1, (
        "a two-parameter predicate is read as (element, index) and silently "
        "compares the score against the index"
    )


def test_no_threshold_means_no_filter_node():
    """The default must leave the plan byte-identical to the pre-threshold one,
    so this parameter cannot be blamed for a change in the J1 parity gate."""
    sig = inspect.signature(batched.score_pairs_batched)
    assert sig.parameters["threshold"].default is None


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
