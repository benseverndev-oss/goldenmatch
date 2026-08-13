"""J0 unit tests: jar discovery, registration wiring, and the id map.

No Spark and no jar needed -- the session is faked, because what is under test is
this module's contract, not Spark's. The real ship-and-call is the lane test.

The load-bearing test here is the scorer-id one: those ids are duplicated from
score-core on purpose (this path must work with no compiled kernel present), and
a duplicated constant that drifts is how two components stop meaning the same
thing while both look fine.
"""
from __future__ import annotations

import pytest
from goldenmatch.spark.jvm import (
    FINGERPRINT_UDF_NAME,
    IMPL_UDF_NAME,
    SCORER_IDS,
    UDF_NAME,
    JvmScorerUnavailable,
    find_jar,
    install,
    scorer_id,
)


class _FakeSession:
    """The two calls `install` makes, and nothing else."""

    def __init__(self, *, artifact_raises=None, register_raises=None):
        self.artifacts: list[str] = []
        self.registered: list[tuple] = []
        self._artifact_raises = artifact_raises
        self._register_raises = register_raises
        outer = self

        class _Udf:
            def registerJavaFunction(self, name, cls, ret):  # noqa: N802 - Spark's name
                if outer._register_raises:
                    raise outer._register_raises
                outer.registered.append((name, cls, ret))

        self.udf = _Udf()

    def addArtifact(self, path):  # noqa: N802 - Spark's name
        if self._artifact_raises:
            raise self._artifact_raises
        self.artifacts.append(path)


@pytest.fixture()
def jar(tmp_path):
    p = tmp_path / "goldenmatch-spark.jar"
    p.write_bytes(b"PK\x03\x04not-a-real-jar")
    return p


# ── jar discovery ────────────────────────────────────────────────────

def test_explicit_path_wins(jar, monkeypatch, tmp_path):
    other = tmp_path / "other.jar"
    other.write_bytes(b"x")
    monkeypatch.setenv("GOLDENMATCH_SPARK_JAR", str(other))
    assert find_jar(jar) == jar


def test_env_var_is_used_when_no_explicit_path(jar, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SPARK_JAR", str(jar))
    assert find_jar() == jar


def test_a_missing_jar_names_every_place_it_looked(monkeypatch, tmp_path):
    """A message that only says 'not found' makes the reader go read the source."""
    monkeypatch.setenv("GOLDENMATCH_SPARK_JAR", str(tmp_path / "nope.jar"))
    with pytest.raises(JvmScorerUnavailable) as err:
        find_jar()
    msg = str(err.value)
    assert "nope.jar" in msg
    assert "GOLDENMATCH_SPARK_JAR" in msg
    assert "packages/jvm" in msg.replace("\\", "/")


def test_discovery_is_anchored_to_the_file_not_the_cwd(monkeypatch, tmp_path):
    """CWD differs between a local run (package dir) and CI (repo root); a
    CWD-relative default would resolve in one and not the other."""
    monkeypatch.delenv("GOLDENMATCH_SPARK_JAR", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(JvmScorerUnavailable) as err:
        find_jar()
    # The built-jar candidate must still point into the repo, not into tmp_path.
    assert str(tmp_path) not in str(err.value)


# ── install ──────────────────────────────────────────────────────────

def test_install_ships_then_registers(jar):
    spark = _FakeSession()
    name = install(spark, jar=jar)

    assert name == UDF_NAME
    assert spark.artifacts == [str(jar)]
    assert spark.registered == [
        (UDF_NAME, "dev.goldensuite.spark.GoldenScoreUdf", "array<double>"),
        (IMPL_UDF_NAME, "dev.goldensuite.spark.GoldenScoreImplUdf", "string"),
        (
            FINGERPRINT_UDF_NAME,
            "dev.goldensuite.spark.GoldenFingerprintUdf",
            "string",
        ),
    ]


def test_every_kernel_the_jar_carries_is_registered_by_install(jar):
    """One `install` call gives a session everything the jar can do.

    A caller should not have to know which kernels exist to get them -- and a
    capability that has to be registered separately is one that silently is not
    there, which for the fingerprint means falling back to a Python worker on a
    cluster that was supposed to need none.
    """
    spark = _FakeSession()
    install(spark, jar=jar)
    names = [r[0] for r in spark.registered]
    for expected in (UDF_NAME, IMPL_UDF_NAME, FINGERPRINT_UDF_NAME):
        assert expected in names, f"{expected} not registered; got {names}"


def test_the_implementation_probe_registers_alongside_the_scorer(jar):
    """J2's jar falls back to the `exact`-only scorer when the native library
    will not load. That keeps a distributed job alive and is otherwise
    invisible -- the query still returns numbers, from a narrower path.

    So the probe that reports which scorer an executor resolved must be
    registered by `install`, not on demand. Registering it later would mean the
    first thing a caller can check is whether the results they already trusted
    came from the kernel.
    """
    spark = _FakeSession()
    install(spark, jar=jar)
    names = [r[0] for r in spark.registered]
    assert IMPL_UDF_NAME in names, (
        f"the implementation probe did not register; a silent fallback to the "
        f"J0 scorer would be undetectable. Registered: {names}"
    )


def test_the_return_type_is_an_array(jar):
    """The whole point is one call per BATCH. A scalar return type would
    silently make it one call per pair."""
    spark = _FakeSession()
    install(spark, jar=jar)
    assert spark.registered[0][2] == "array<double>"


def test_a_classic_session_is_reported_as_unavailable_with_the_reason(jar):
    """`addArtifact` is Connect-only and raises on a classic session. The error
    must say that, not just propagate."""
    spark = _FakeSession(artifact_raises=RuntimeError("not supported"))
    with pytest.raises(JvmScorerUnavailable, match="Connect"):
        install(spark, jar=jar)


def test_a_registration_failure_is_typed_not_bare(jar):
    """Callers fall back to the pandas_udf path on this type; a bare Exception
    would force them to catch everything, including their own bugs."""
    spark = _FakeSession(register_raises=RuntimeError("boom"))
    with pytest.raises(JvmScorerUnavailable, match="GoldenScoreUdf"):
        install(spark, jar=jar)


def test_nothing_registers_when_the_jar_cannot_be_shipped(jar):
    spark = _FakeSession(artifact_raises=RuntimeError("nope"))
    with pytest.raises(JvmScorerUnavailable):
        install(spark, jar=jar)
    assert spark.registered == [], "registered a UDF whose jar never arrived"


# ── scorer ids ───────────────────────────────────────────────────────

def test_ids_match_the_native_loaders_map():
    """These are duplicated from score-core on purpose -- this path must work
    with no compiled kernel present. A duplicated constant that drifts is how
    two components stop meaning the same thing while both look fine."""
    from goldenmatch.spark.scorers import _NATIVE_SCORER_IDS

    for name, native_id in _NATIVE_SCORER_IDS.items():
        assert SCORER_IDS[name] == native_id, (
            f"{name}: jvm.py says {SCORER_IDS.get(name)}, "
            f"scorers.py says {native_id}"
        )


def test_exact_is_id_three():
    """J0's only supported scorer, and the one the Java side implements."""
    assert scorer_id("exact") == 3


def test_an_unknown_scorer_is_refused_not_defaulted():
    """An unrecognised id would be scored by the kernel's catch-all arm -- a
    silently wrong number rather than a failure."""
    with pytest.raises(ValueError, match="unknown scorer"):
        scorer_id("definitely_not_a_scorer")


# ── the pure floor must equal the kernel exactly (J2) ────────────────
#
# The JVM path computes `score_one` -- the same dispatcher behind pyo3 `native`,
# `datafusion-udf` and `score-wasm`. So "the JVM agrees with Python" is only a
# meaningful claim if the tier's own Python scorer agrees with `score_one` too.
# Measured while building J2's parity gate, it did not: `token_sort` came back
# 0.923076923076923 from the pure floor and 0.9230769230769231 from the kernel.
#
# Cause: the floor called the 0-100 `token_sort_ratio` and divided by 100, and
# `(x * 100) / 100` is not `x` in binary floating point. One ULP, and entirely
# self-inflicted -- there was no reason to scale up and back down.
#
# One ULP sounds ignorable, and for a score you REPORT it is. This tier does not
# report these; it thresholds them. `scorers.py`'s own module docstring makes the
# argument, about f32: "a tolerance is fine for a score you report and not for
# one you THRESHOLD". Same argument, smaller number.
#
# No Spark, no jar, no native wheel needed -- this is arithmetic.

def test_the_pure_floor_does_not_round_trip_token_sort_through_0_100():
    """The specific defect: scale up to 0-100, divide back, lose a bit."""
    from goldenmatch.core import strsim
    from goldenmatch.spark.scorers import _pure_scores

    # 6 shared tokens of 13 total characters -> exactly 12/13.
    a, b = ["日本語テキスト"], ["日本語テスト"]
    got = _pure_scores("token_sort", a, b)[0]

    xs = " ".join(sorted(a[0].split()))
    ys = " ".join(sorted(b[0].split()))
    want = strsim.indel_normalized_similarity(xs, ys)

    assert got == want, (
        f"the floor returned {got!r}, the unscaled similarity is {want!r}. "
        f"A 0-100 round trip is the only difference, and it costs a bit that "
        f"the kernel does not lose."
    )
    # And the round trip is demonstrably lossy, so the test above is not vacuous.
    assert strsim.token_sort_ratio(xs, ys) / 100.0 != want


def test_token_sort_similarity_and_ratio_stay_in_lockstep():
    """The 0-100 scorer keeps its rapidfuzz parity: it is the [0, 1] value
    scaled, not a second implementation."""
    from goldenmatch.core import strsim

    for a, b in [
        ("alice smith", "smith alice"),
        ("日本語テキスト", "日本語テスト"),
        ("", ""),
        ("one", "one two three"),
        ("Foo", "foo"),
    ]:
        assert strsim.token_sort_ratio(a, b) == strsim.token_sort_similarity(a, b) * 100.0
