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


# --- Exact distributed profiling (spec 2026-08-20) ------------------------
#
# The two shapes below are the bugs this exists for, both already documented in
# the source. Each is measured at MORE THAN ONE SCALE on purpose: the defect is
# that the verdict moves with scale, so a single-scale assertion cannot see it.


def _prof(name: str, *, cardinality_ratio: float, n_distinct=None, null_rate=0.0, avg_len=8.0):
    from goldenmatch.core.autoconfig import ColumnProfile

    return ColumnProfile(
        name=name, dtype="str", col_type="identifier", confidence=0.9,
        sample_values=["a", "b"], null_rate=null_rate,
        cardinality_ratio=cardinality_ratio, avg_len=avg_len, n_distinct=n_distinct,
    )


def test_876_cardinality_ratio_is_scale_invariant_once_exact():
    """The #876 surrogate guard: a flat 0.28 read as 0.72 -> 1.00 across scale.

    `autoconfig.py` records an `email` column whose TRUE distinct-fraction is a
    flat 0.28 reading 0.72 / 0.94 / 0.97 / 0.98 / 1.00 at 100K / 500K / 1M / 2M
    / 5M, because a fixed-size sample of a growing frame drives the fraction to
    1.0. Every surrogate guard compares that to an absolute 1.0, so at 5M
    zero-config discarded its only exact identity column and produced a 185M-pair
    run that killed the runner.

    Exact stats must read ~0.28 at EVERY scale.
    """
    from goldenmatch.spark.autoconfig import ExactStats, merge_exact_stats

    sampled_ratio_by_scale = {100_000: 0.72, 500_000: 0.94, 1_000_000: 0.97,
                              2_000_000: 0.98, 5_000_000: 1.00}
    for n_full, sampled in sampled_ratio_by_scale.items():
        profiles = [_prof("email", cardinality_ratio=sampled)]
        exact = {"email": ExactStats(
            n_rows=n_full, n_non_null=n_full,
            n_distinct=int(0.28 * n_full), avg_len=18.0,
        )}
        out = merge_exact_stats(profiles, exact, n_full=n_full)
        assert abs(out[0].cardinality_ratio - 0.28) < 0.01, (
            f"at n={n_full:,} the ratio must be the TRUE 0.28, "
            f"not the sampled {sampled}"
        )


def test_2687_a_nearly_constant_column_is_not_reported_constant():
    """A 99.99% constant column must not read `n_distinct == 1`.

    PR #2687 drops a scored field when `n_distinct <= 1`, and that count came
    from a 1,000-row sample: a value at frequency 1e-4 is missed 90% of the
    time. The field then gets dropped at 5M and kept at 2,000 rows -- same data,
    different config.
    """
    from goldenmatch.spark.autoconfig import ExactStats, merge_exact_stats

    n_full = 5_000_000
    profiles = [_prof("status", cardinality_ratio=1 / 1000, n_distinct=1)]
    exact = {"status": ExactStats(n_rows=n_full, n_non_null=n_full, n_distinct=2, avg_len=6.0)}

    out = merge_exact_stats(profiles, exact, n_full=n_full)
    assert out[0].n_distinct == 2, "the rare second value must survive profiling"


def test_exact_and_sampled_agree_when_the_sample_IS_the_frame():
    """The control. If they disagree here, the exact path is wrong, not the sample."""
    from goldenmatch.spark.autoconfig import ExactStats, merge_exact_stats

    n_full = 500
    profiles = [_prof("city", cardinality_ratio=40 / 500, n_distinct=40, null_rate=0.1, avg_len=7.0)]
    exact = {"city": ExactStats(n_rows=500, n_non_null=450, n_distinct=40, avg_len=7.0)}

    out = merge_exact_stats(profiles, exact, n_full=n_full)
    assert out[0].n_distinct == 40
    assert abs(out[0].cardinality_ratio - 40 / 500) < 1e-9
    assert abs(out[0].null_rate - 0.1) < 1e-9


def test_classification_fields_are_NEVER_overwritten():
    """Only distribution stats go exact. Type detection stays on the sample.

    `col_type`, `confidence` and `sample_values` answer "what kind of column is
    this", which a sample answers correctly by construction. Overwriting them
    from an aggregate would be a different (and wrong) change.
    """
    from goldenmatch.spark.autoconfig import ExactStats, merge_exact_stats

    profiles = [_prof("email", cardinality_ratio=1.0)]
    before = (profiles[0].col_type, profiles[0].confidence, list(profiles[0].sample_values))
    exact = {"email": ExactStats(n_rows=1_000_000, n_non_null=999_000, n_distinct=280_000, avg_len=18.0)}

    out = merge_exact_stats(profiles, exact, n_full=1_000_000)
    assert (out[0].col_type, out[0].confidence, list(out[0].sample_values)) == before


def test_a_column_with_no_exact_stats_is_left_untouched():
    """Absent is not zero. A column the exact pass skipped keeps its sampled
    values rather than being rewritten to 0 -- the same absent-vs-zero
    discipline as `candidates_counted` and the gce_detached liveness probe."""
    from goldenmatch.spark.autoconfig import ExactStats, merge_exact_stats

    profiles = [_prof("email", cardinality_ratio=0.9, n_distinct=900),
                _prof("notes", cardinality_ratio=0.5, n_distinct=500)]
    exact = {"email": ExactStats(n_rows=1000, n_non_null=1000, n_distinct=280, avg_len=18.0)}

    out = merge_exact_stats(profiles, exact, n_full=1000)
    assert out[1].n_distinct == 500, "the skipped column must keep its sampled stats"
    assert abs(out[1].cardinality_ratio - 0.5) < 1e-9


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
