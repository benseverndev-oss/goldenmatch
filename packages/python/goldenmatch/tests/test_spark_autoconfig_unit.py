"""P6 unit tests: the scale-safety logic of zero-config on Spark.

No Spark needed -- the Spark DataFrame is faked, because what is under test is
the arithmetic and the refusal, not Spark itself. The end-to-end run is covered
in the lane test.

The thing worth testing here is not "does it produce a config" but "does it
refuse to pretend a 20k glance describes 500M rows". The controller's confidence
gate reads the row count off the frame it is handed, so on a sample it reads the
SAMPLE size -- meaning the run that most needs the refusal is exactly the one
that would skip it.
"""
from __future__ import annotations

import pytest
from goldenmatch.spark.autoconfig import (
    DEFAULT_SAMPLE_ROWS,
    SparkAutoConfigTooLarge,
    SparkAutoConfigUnsupported,
    _refuse_at_n,
    auto_configure_spark,
    sample_to_driver,
)


class _FakeSpark:
    """The narrow slice of the DataFrame API `sample_to_driver` uses.

    `sample` records the fraction it was asked for so a test can assert the tier
    sampled rather than truncated -- the distinction that decides whether the
    profile describes the dataset or its first partition.
    """

    def __init__(self, rows: list[dict], *, n: int | None = None):
        self._rows = rows
        self._n = n if n is not None else len(rows)
        self.sample_calls: list[dict] = []
        self.limit_calls: list[int] = []

    def count(self) -> int:
        return self._n

    def sample(self, withReplacement, fraction, seed):  # noqa: N803 (Spark's name)
        self.sample_calls.append(
            {"withReplacement": withReplacement, "fraction": fraction, "seed": seed}
        )
        # Sample against the CLAIMED row count, not the rows this fake happens to
        # hold. A test can declare "500M rows" while carrying 200 of them, and
        # real Spark would return `fraction * 500M`; scaling by the held count
        # instead would return 1 row and quietly make every downstream assertion
        # about sample size meaningless.
        keep = min(len(self._rows), max(1, int(round(self._n * fraction))))
        out = _FakeSpark(self._rows[:keep], n=self._n)
        out.sample_calls = self.sample_calls
        out.limit_calls = self.limit_calls
        return out

    def limit(self, n):
        self.limit_calls.append(n)
        return _FakeSpark(self._rows[:n], n=self._n)

    def collect(self):
        class _Row(dict):
            def asDict(self):
                return dict(self)

        return [_Row(r) for r in self._rows]


def _rows(n: int) -> list[dict]:
    """Deliberately NEUTRAL column names (`code`/`zone`, not `name`/`city`).

    Auto-config picks scorers from column semantics, and on a name-like column it
    chooses `given_name_aliased_jw` -- a reference-table-backed scorer the Spark
    tier cannot dispatch, so `auto_configure_spark` refuses the config it just
    produced. That refusal is correct and is covered by the lane test; here it
    would only obscure what these tests are about, which is the SCALE arithmetic.
    Rename these columns and most of this file starts failing for a reason that
    has nothing to do with what it asserts.
    """
    return [{"__row_id__": i, "code": f"n{i}", "zone": f"z{i % 7}"} for i in range(n)]


# ── sampling ─────────────────────────────────────────────────────────

def test_small_input_is_taken_whole_without_sampling():
    df = _FakeSpark(_rows(50))
    table, n_full = sample_to_driver(df, n_target=1000)
    assert n_full == 50
    assert table.num_rows == 50
    assert df.sample_calls == [], "no need to sample when the whole thing fits"


def test_large_input_is_sampled_not_truncated():
    """`limit(n)` returns whatever the first partitions hold, and partitions are
    usually ordered by ingestion, source or date -- so a limit-sample of a
    partitioned table is a sample of its oldest rows. Profiling that and calling
    it a dataset profile is how a column looks unique when it is unique only
    within one partition."""
    df = _FakeSpark(_rows(1000), n=1_000_000)
    sample_to_driver(df, n_target=100)

    assert df.limit_calls == [], "must not truncate; limit is partition-ordered"
    assert len(df.sample_calls) == 1
    call = df.sample_calls[0]
    assert call["withReplacement"] is False
    assert call["fraction"] == pytest.approx(100 / 1_000_000)


def test_the_sample_is_deterministic_for_a_given_seed():
    df1, df2 = _FakeSpark(_rows(1000), n=10_000), _FakeSpark(_rows(1000), n=10_000)
    sample_to_driver(df1, n_target=100, seed=7)
    sample_to_driver(df2, n_target=100, seed=7)
    assert df1.sample_calls[0]["seed"] == df2.sample_calls[0]["seed"] == 7


def test_the_true_row_count_is_reported_not_the_sample_size():
    """The number everything downstream depends on."""
    df = _FakeSpark(_rows(1000), n=987_654_321)
    _table, n_full = sample_to_driver(df, n_target=100)
    assert n_full == 987_654_321


def test_an_empty_dataframe_is_refused():
    with pytest.raises(ValueError, match="empty"):
        sample_to_driver(_FakeSpark([], n=0))


# ── the scale refusal ────────────────────────────────────────────────

def test_a_large_dataset_is_refused_without_an_explicit_opt_in():
    """The core of P6.

    The controller refuses a RED config at >= REFUSE_AT_N rows, but reads the
    row count off the frame it is given. On a sample it sees the sample size, so
    the gate cannot fire. This refusal stands in for it, against the true count.
    """
    n_full = _refuse_at_n() + 1
    df = _FakeSpark(_rows(500), n=n_full)
    with pytest.raises(SparkAutoConfigTooLarge) as err:
        auto_configure_spark(df, n_sample=100)

    msg = str(err.value)
    assert f"{n_full:,}" in msg, "the message must state the real row count"
    assert "allow_large=True" in msg, "and how to proceed"


def test_a_dataset_below_the_threshold_passes_the_scale_gate():
    """A guard that refused everything would pass the test above while making
    zero-config unusable, so the sub-threshold case must get PAST the scale gate.

    It may still be refused afterwards for a different reason:
    `SparkAutoConfigUnsupported` fires when auto-config picks a feature outside
    the tier's surface, which it genuinely does (observed: `given_name_aliased_jw`
    on name-like columns, `learned` blocking here). That is a real gap, covered
    by the lane test. What must NOT happen is `SparkAutoConfigTooLarge` -- this
    dataset is under the threshold, and conflating "too big" with "unsupported"
    would send the caller to `allow_large=True`, which cannot help them.
    """
    df = _FakeSpark(_rows(200), n=_refuse_at_n() - 1)
    try:
        cfg, prov = auto_configure_spark(df, n_sample=200)
    except SparkAutoConfigTooLarge:  # pragma: no cover - the failure this pins
        pytest.fail("refused a dataset below the controller's own threshold")
    except SparkAutoConfigUnsupported:
        return  # past the scale gate, which is what this test is about
    assert cfg.get_matchkeys(), "auto-config returned no matchkeys"
    assert prov["n_full"] == _refuse_at_n() - 1


def test_allow_large_opts_in_and_is_recorded():
    """Opting in must be visible afterwards: a config whose origin is not
    recorded gets treated as though someone chose it deliberately."""
    n_full = _refuse_at_n() + 5
    df = _FakeSpark(_rows(200), n=n_full)
    try:
        _cfg, prov = auto_configure_spark(df, n_sample=200, allow_large=True)
    except SparkAutoConfigTooLarge:  # pragma: no cover
        pytest.fail("allow_large=True did not open the scale gate")
    except SparkAutoConfigUnsupported:
        # The gate opened -- the later refusal is about the tier's surface, not
        # about size, and is the lane test's subject.
        return

    assert prov["allow_large"] is True
    assert prov["n_full"] == n_full
    assert prov["n_sampled"] == 200
    assert prov["source"] == "spark-sample"
    assert prov["fraction"] == pytest.approx(200 / n_full)


def test_the_full_row_count_reaches_auto_config_not_the_sample_size(monkeypatch):
    """`n_rows_full` is Chao1's denominator. Without it, a sampled
    mid-cardinality column (zip) looks near-unique -- and near-unique columns get
    picked as blocking keys, which on the full data yields blocks of one and
    finds nothing.
    """
    seen = {}

    import goldenmatch.core.autoconfig as ac

    real = ac.auto_configure_df

    def _spy(table, **kw):
        seen.update(kw)
        seen["sampled_rows"] = table.num_rows
        return real(table, **kw)

    monkeypatch.setattr(ac, "auto_configure_df", _spy)

    n_full = _refuse_at_n() - 1
    # The spy records BEFORE the tier-surface validation, so an unsupported
    # config does not stop this from observing what auto-config was told.
    try:
        auto_configure_spark(_FakeSpark(_rows(200), n=n_full), n_sample=200)
    except SparkAutoConfigUnsupported:
        pass

    assert seen["n_rows_full"] == n_full, (
        f"auto-config was told {seen.get('n_rows_full')!r} rows; the sample was "
        f"{seen.get('sampled_rows')!r} and the dataset is {n_full}"
    )
    assert seen["sampled_rows"] == 200


def test_the_threshold_tracks_the_controller_rather_than_a_local_literal():
    """If REFUSE_AT_N moves, this must move with it -- a duplicated literal is
    how two gates that are supposed to agree stop agreeing."""
    from goldenmatch.core.autoconfig_controller import REFUSE_AT_N

    assert _refuse_at_n() == REFUSE_AT_N


def test_default_sample_size_is_in_the_controllers_own_range():
    """The controller's budget tiers cap sub-samples at 20k; sampling far beyond
    that buys little and costs a bigger collect."""
    assert 1_000 <= DEFAULT_SAMPLE_ROWS <= 50_000


# --- Exact distributed profiling: the COST decision (spec 2026-08-20) ---
#
# The merge rules themselves live in `core.autoconfig` and are tested through
# the real `profile_columns` path in test_exact_profiling_override.py -- the
# shipped basis, not a lookalike of it. What is unit-tested here is the part
# that is Spark's own: which columns are worth an exact count_distinct.


def test_only_boundary_columns_pay_for_an_exact_count_distinct():
    """`count_distinct` is a shuffle per column and is the whole cost.

    HyperLogJog is a few percent out, which is fine mid-range and useless at
    exactly the two cuts auto-config turns on: `<= 1` (constant) and near-unique
    (surrogate key). Only those nominate for an exact count.
    """
    from goldenmatch.spark.autoconfig import _boundary_columns

    n_full = 1_000_000
    approx = {
        "status": 1,          # constant -> boundary
        "flag": 2,            # near-constant -> boundary
        "email": 999_000,     # near-unique -> boundary
        "city": 40,           # mid-range -> NOT boundary
        "zip": 500,           # mid-range -> NOT boundary
    }
    got = set(_boundary_columns(approx, n_full))
    assert got == {"status", "flag", "email"}, got


def test_boundary_selection_is_empty_on_an_unknown_row_count():
    """No row count means no ratio, so nothing can be called near-unique.

    Guessing here would nominate every column for the expensive pass.
    """
    from goldenmatch.spark.autoconfig import _boundary_columns

    assert _boundary_columns({"a": 5, "b": 900}, 0) == []


def test_exact_stats_returns_empty_rather_than_raising_without_pyspark(monkeypatch):
    """A failed aggregate must leave the sampled statistics in place.

    Profiling is instrumentation for a config decision. An ABSENT statistic is
    handled by `merge_exact_stats` (it leaves the column alone); a raised one
    would take down a run that could have proceeded on sampled values.
    """
    from goldenmatch.spark import autoconfig as sac

    class _Boom:
        def agg(self, *a, **k):
            raise RuntimeError("cluster went away")

    assert sac.exact_column_stats(_Boom(), ["email"]) == {}


def test_exact_profiling_is_OFF_by_default(monkeypatch):
    """Default OFF is the whole safety property of this slice.

    Several rules compare these fields to cuts chosen while the inputs were
    sample-derived, so making them truthful can move quality scores -- as
    rebasing `mass_above_threshold` took `anchor_person_match` from 1.0000 to
    0.7303. It flips on quality-gate evidence, not on being more correct.
    """
    from goldenmatch.spark.autoconfig import _exact_profiling_enabled

    monkeypatch.delenv("GOLDENMATCH_SPARK_EXACT_PROFILING", raising=False)
    assert _exact_profiling_enabled(None) is False


def test_the_argument_overrides_the_environment_both_ways(monkeypatch):
    """A caller or a test must be able to pin it without touching process state."""
    from goldenmatch.spark.autoconfig import _exact_profiling_enabled

    monkeypatch.setenv("GOLDENMATCH_SPARK_EXACT_PROFILING", "1")
    assert _exact_profiling_enabled(None) is True
    assert _exact_profiling_enabled(False) is False, "explicit False must win"

    monkeypatch.setenv("GOLDENMATCH_SPARK_EXACT_PROFILING", "0")
    assert _exact_profiling_enabled(None) is False
    assert _exact_profiling_enabled(True) is True, "explicit True must win"
