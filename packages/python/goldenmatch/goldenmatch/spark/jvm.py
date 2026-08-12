"""J0: ship the JVM scorer jar to a Spark session and register it.

The tier scores by forking a Python worker per batch (`pandas_udf`): an Arrow IPC
hop plus an interpreter, and the sole reason P1 ships a Python environment to
executors at all. Executors are JVMs and can call the Rust kernel directly. This
module is the client half of that -- deliver the jar, register the UDF.

**Proven over Spark Connect before it was written** (probe run 31611464914):
`addArtifact` accepts a jar, `registerJavaFunction` registers *and calls* it, and
an array-shaped UDF carries 10,000 pairs in one call. That last one is what makes
the approach worth anything: Connect only permits row-shaped UDFs, so without
batching every pair would cost a downcall into native code.

**J0 carries no scoring algorithms.** The jar implements `exact` only, and
refuses every other scorer loudly. A Java jaro-winkler would be a fourth
implementation of a kernel that already exists once in Rust, which is the thing
this whole arc argues against; `exact` escapes that because string equality is
identical by inspection in any language. The kernel arrives in J2 via JNI into
`score-cabi`.

So nothing routes through here yet by default. J0's job is to prove the plumbing
with no native call in the picture, so that when the plan reshape lands in J1 a
misalignment cannot be confused with a kernel bug.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Override the jar location. Set by CI, which builds the jar rather than
#: vendoring one.
JAR_ENV = "GOLDENMATCH_SPARK_JAR"

#: The SQL name the batch scorer registers under.
UDF_NAME = "golden_score_batch"

_UDF_CLASS = "dev.goldensuite.spark.GoldenScoreUdf"

#: Where the build puts the jar, relative to the repo root.
_BUILT_JAR = Path("packages/jvm/goldenmatch-spark/build/goldenmatch-spark.jar")

#: score-core's ids. Duplicated from `score_one` deliberately and NOT imported
#: from the native loader: this path must work with no compiled kernel present
#: (that is the whole point of J0). `test_spark_jvm_unit.py` pins them against
#: the loader's map so the duplication cannot drift.
SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0,
    "levenshtein": 1,
    "token_sort": 2,
    "exact": 3,
}


class JvmScorerUnavailable(RuntimeError):
    """The jar could not be found, shipped, or registered.

    Its own type so callers can fall back to the `pandas_udf` path rather than
    catching a bare Exception. A distributed run must not fail because one
    executor could not load a library -- correctness first, speed second.
    """


def find_jar(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate the scorer jar: explicit argument, then ``GOLDENMATCH_SPARK_JAR``,
    then the build output.

    Raises rather than returning ``None`` so a missing jar is a message naming
    every place that was checked, instead of a ``NoneType`` surfacing later
    inside a Spark call.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get(JAR_ENV)
    if env:
        candidates.append(Path(env))
    # Repo-root-relative, resolved from this file rather than the CWD: the CWD
    # differs between a local run (package dir) and CI (repo root), which has
    # bitten fixture paths in this repo before.
    repo_root = Path(__file__).resolve().parents[4]
    candidates.append(repo_root / _BUILT_JAR)

    for c in candidates:
        if c.is_file():
            return c
    raise JvmScorerUnavailable(
        "JVM scorer jar not found. Looked in:\n"
        + "\n".join(f"  - {c}" for c in candidates)
        + f"\nBuild it, or set {JAR_ENV}. The jar is built in CI rather than "
        f"vendored, so a source checkout will not have one until you build it."
    )


def install(spark: object, *, jar: str | os.PathLike[str] | None = None,
            name: str = UDF_NAME) -> str:
    """Ship the jar to ``spark`` and register the batch scorer. Returns the SQL
    name it registered under.

    Both steps are Spark **Connect** capabilities confirmed by probe; on a
    classic session `addArtifact` raises, which is reported as
    :class:`JvmScorerUnavailable` rather than propagating a Spark error that
    says nothing about what to do next.
    """
    path = find_jar(jar)
    try:
        spark.addArtifact(str(path))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed failure
        raise JvmScorerUnavailable(
            f"could not ship {path} to the session: {type(exc).__name__}: {exc}. "
            f"`addArtifact` is Spark Connect-only -- it raises on a classic "
            f"session, so check the session was built with `.remote(...)`."
        ) from exc

    try:
        spark.udf.registerJavaFunction(  # type: ignore[attr-defined]
            name, _UDF_CLASS, "array<double>"
        )
    except Exception as exc:  # noqa: BLE001
        raise JvmScorerUnavailable(
            f"could not register {_UDF_CLASS} as {name!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    logger.info("Spark JVM scorer: shipped %s and registered %r", path, name)
    return name


def scorer_id(name: str) -> int:
    """score-core's id for a scorer name.

    Raises on an unknown name rather than defaulting: an unrecognised id would
    be scored by whatever the kernel's catch-all arm does, which is a silently
    wrong number rather than a failure.
    """
    try:
        return SCORER_IDS[name]
    except KeyError:
        raise ValueError(
            f"unknown scorer {name!r}; the JVM path knows "
            f"{sorted(SCORER_IDS)}. Ids must match score_one in "
            f"packages/rust/extensions/score-core/src/lib.rs."
        ) from None
