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
        (UDF_NAME, "dev.goldensuite.spark.GoldenScoreUdf", "array<double>")
    ]


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
