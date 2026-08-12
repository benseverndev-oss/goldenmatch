"""P6: zero-config on Spark.

Auto-config profiles the data, picks blocking keys, matchkeys, scorers and
thresholds. On a cluster the data is not on the driver, so something has to give.
What gives here is the *sample*, not the *row count* -- and keeping those two
apart is the whole difficulty.

**Auto-config already runs on a sample.** `auto_configure_df` profiles and runs
blocking -> score -> cluster on a stratified sub-sample, so sampling is the
existing mechanism rather than a concession made for Spark. The same was true of
EM in P5. What is NOT already handled is that the controller infers the dataset's
size from the frame it is handed:

    autoconfig_controller.py:609   n_rows = _to_frame_gate(df).height

and then uses that number for two things that must see the FULL population:

1. **The confidence gate.** `n_rows >= REFUSE_AT_N (100_000)` with a RED config
   raises `ControllerNotConfidentError`. Hand it a 50k sample drawn from 500M
   rows and the gate reads 50k, so it never fires -- the run that most needs the
   refusal is the one that silently skips it.
2. **Chao1 cardinality extrapolation.** The controller's own comment says this
   "depend[s] on this being the FULL data count, not the sample size". At sample
   scale a real mid-cardinality column (zip) looks near-unique, and near-unique
   columns get chosen as blocking keys -- which on the full data produces blocks
   of one and finds nothing.

`auto_configure_df(n_rows_full=...)` fixes only (2), and only for the v0
heuristic; it does not move the controller's gate. So this module applies the
scale check ITSELF, against the true count, before handing anything over.

The result is a config derived from a sample and *labelled as such*, with the
large-dataset case requiring an explicit opt-in rather than quietly producing
recommendations for 500M rows from a 50k glance.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Rows pulled to the driver to profile. Matches the controller's own sampling
# scale (its budget tiers cap sub-samples at 20k); larger buys little and costs
# a bigger collect.
DEFAULT_SAMPLE_ROWS = 20_000

# Mirrors autoconfig_controller.REFUSE_AT_N. Imported at call time rather than
# duplicated as a literal, so the two cannot drift.
_SEED = 42


class SparkAutoConfigUnsupported(RuntimeError):
    """Zero-config produced a config the Spark tier cannot execute.

    Distinct from :class:`SparkAutoConfigTooLarge` because the remedies differ:
    that one is answered by `allow_large=True`, this one by supplying a config
    within the tier's surface (or by widening the tier).
    """


class SparkAutoConfigTooLarge(RuntimeError):
    """Zero-config was asked to configure a dataset large enough that the
    controller's own confidence gate would have refused it -- had the gate been
    able to see the real row count."""


def _refuse_at_n() -> int:
    from goldenmatch.core.autoconfig_controller import REFUSE_AT_N

    return int(REFUSE_AT_N)


def sample_to_driver(
    spark_df: Any,
    *,
    n_target: int = DEFAULT_SAMPLE_ROWS,
    seed: int = _SEED,
) -> tuple[Any, int]:
    """``(arrow_table, n_full)`` -- a random sample on the driver plus the true
    cluster-side row count.

    Bernoulli-sampled with `DataFrame.sample`, NOT `limit`. `limit(n)` returns
    whatever the first partitions hold, and partitions are usually ordered by
    ingestion, source, or date -- so a `limit` sample of a partitioned table is
    a sample of its oldest rows, or of one source. Profiling that and calling
    the result a dataset profile is how you conclude a column is unique when it
    is unique only within one partition.

    The sample size is therefore approximate (Bernoulli variance) rather than
    exactly ``n_target``. That is the right trade: an exact count is only
    obtainable by truncating, and truncating a collected sample re-introduces
    the partition bias the sampling just removed.
    """
    import pyarrow as pa

    n_full = int(spark_df.count())
    if n_full <= 0:
        raise ValueError("cannot auto-configure an empty DataFrame")

    if n_full <= n_target:
        sampled = spark_df
        fraction = 1.0
    else:
        fraction = n_target / n_full
        sampled = spark_df.sample(withReplacement=False, fraction=fraction, seed=seed)

    rows = [r.asDict() for r in sampled.collect()]
    if not rows:
        raise ValueError(
            f"the sample came back empty (n_full={n_full}, fraction={fraction:.6g}); "
            f"raise n_target or check the source DataFrame"
        )
    logger.info(
        "Spark zero-config: sampled %d of %d rows (fraction=%.6g, seed=%d)",
        len(rows), n_full, fraction, seed,
    )
    return pa.Table.from_pylist(rows), n_full


def auto_configure_spark(
    spark_df: Any,
    *,
    n_sample: int = DEFAULT_SAMPLE_ROWS,
    seed: int = _SEED,
    allow_large: bool = False,
    allow_red_config: bool = False,
    **kwargs: Any,
) -> tuple[Any, dict]:
    """``(config, provenance)`` -- zero-config for a Spark DataFrame.

    Profiles a driver-side sample with the ordinary `auto_configure_df`, so every
    heuristic, refit loop and health signal is the one-box's. The two things this
    adds are the ones sampling breaks: the true row count is passed through as
    ``n_rows_full`` (Chao1's denominator), and the scale refusal is applied here
    against that count instead of being left to a gate that would only ever see
    the sample.

    ``allow_large`` is the explicit opt-in for a dataset at or above the
    controller's refuse threshold. It is not a formality: a config chosen from
    20k rows and applied to 500M is a real risk, and the caller should be the one
    accepting it.

    ``provenance`` records what the config was derived from -- sample size, full
    count, fraction, seed -- because a config whose origin is not recorded gets
    treated as though someone chose it.
    """
    from goldenmatch.core.autoconfig import auto_configure_df

    # COUNT, REFUSE, THEN SAMPLE -- in that order. Sampling first pulled rows to
    # the driver that were about to be thrown away, and worse, it let a sampling
    # failure mask the refusal: a large declared count makes the fraction tiny,
    # the sample can come back empty, and the caller then sees "the sample came
    # back empty" instead of "this dataset is too large to zero-config".
    # A refusal must not depend on the success of work done only to be discarded.
    n_full = int(spark_df.count())
    if n_full <= 0:
        raise ValueError("cannot auto-configure an empty DataFrame")

    refuse_at = _refuse_at_n()
    if n_full >= refuse_at and not allow_large:
        raise SparkAutoConfigTooLarge(
            f"zero-config would derive a config for {n_full:,} rows from a "
            f"~{min(n_sample, n_full):,}-row sample. The controller refuses a "
            f"RED config at >= {refuse_at:,} rows, but it infers the row count "
            f"from the frame it is given -- so on a sample it would see the "
            f"SAMPLE size and skip that check entirely.\n"
            f"Pass allow_large=True to accept a sample-derived config at this "
            f"scale, or supply an explicit GoldenMatchConfig."
        )

    table, sampled_n_full = sample_to_driver(
        spark_df, n_target=n_sample, seed=seed
    )
    n_sampled = table.num_rows
    # `sample_to_driver` counts again; if the two disagree the DataFrame is not
    # stable under repeated evaluation and every number below is suspect.
    if sampled_n_full != n_full:
        logger.warning(
            "Spark zero-config: row count changed between the scale check (%d) "
            "and sampling (%d); the source is not stable under re-evaluation",
            n_full, sampled_n_full,
        )

    logger.info(
        "Spark zero-config: profiling %d sampled rows, n_rows_full=%d",
        n_sampled, n_full,
    )
    config = auto_configure_df(
        table,
        n_rows_full=n_full,
        allow_red_config=allow_red_config,
        **kwargs,
    )
    # VALIDATE THE OUTPUT AGAINST THE TIER, HERE.
    #
    # Auto-config optimises for quality on the one-box, whose surface is larger
    # than this tier's: on a name-heavy fixture it picks `given_name_aliased_jw`,
    # a reference-table-backed scorer that `score_one` (stateless) cannot
    # dispatch at all. Without this check the caller gets a config that looks
    # fine and fails several stages later, inside `run_config_pipeline`, with an
    # error naming a scorer they never chose.
    #
    # Raising here says the true thing: zero-config CAN produce a config this
    # tier cannot execute, and that is a gap in the tier rather than a bad
    # config. Constraining auto-config's search to the tier's scorer set is the
    # real fix and is not a one-liner -- it changes which config is optimal, so
    # it needs its own measurement.
    from goldenmatch.spark.config_pipeline import _validate_spark_config_supported

    try:
        _validate_spark_config_supported(config)
    except (NotImplementedError, ValueError) as exc:
        raise SparkAutoConfigUnsupported(
            f"zero-config chose a config this tier cannot execute: {exc}\n"
            f"Auto-config optimises against the one-box surface, which is wider "
            f"than the Spark tier's. Supply an explicit GoldenMatchConfig using "
            f"only the tier's supported features, or run this dataset on the "
            f"one-box path."
        ) from exc

    provenance = {
        "source": "spark-sample",
        "n_full": n_full,
        "n_sampled": n_sampled,
        "fraction": (n_sampled / n_full) if n_full else 1.0,
        "seed": seed,
        "allow_large": allow_large,
    }
    logger.info("Spark zero-config: committed config from %s", provenance)
    return config, provenance
