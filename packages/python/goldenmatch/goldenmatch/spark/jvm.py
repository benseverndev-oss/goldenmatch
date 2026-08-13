"""Ship the JVM scorer jar to a Spark session and register it (J0), now backed
by the Rust kernel over JNI (J2).

The tier scores by forking a Python worker per batch (`arrow_udf`): an Arrow IPC
hop plus an interpreter, and the sole reason P1 ships a Python environment to
executors at all. Executors are JVMs and can call the Rust kernel directly. This
module is the client half of that -- deliver the jar, register the UDF.

**Proven over Spark Connect before it was written** (probe run 31611464914):
`addArtifact` accepts a jar, `registerJavaFunction` registers *and calls* it, and
an array-shaped UDF carries 10,000 pairs in one call. That last one is what makes
the approach worth anything: Connect only permits row-shaped UDFs, so without
batching every pair would cost a downcall into native code.

**The jar carries no scoring algorithms of its own -- it calls the Rust one.**
J0 shipped `exact` only and refused everything else, deliberately: a Java
jaro-winkler would have been a fourth implementation of a kernel that already
exists once in Rust, which is the thing this whole arc argues against. J2 lifts
the restriction the right way round, by reaching the kernel rather than copying
it -- `NativeScorer` -> JNI -> `score-cabi` -> `score_one`, the same dispatcher
behind pyo3 `native`, `datafusion-udf` and `score-wasm`.

The library rides inside the jar and is extracted per JVM, so `addArtifact`
delivers everything: one file, nothing installed on the cluster, no
`java.library.path` flags. When it will not load the jar falls back to the
`exact`-only scorer so a distributed job survives -- and :func:`implementation`
exists so that fallback can never pass for a native run.
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

#: The SQL name of the probe reporting which scorer an EXECUTOR resolved.
IMPL_UDF_NAME = "golden_score_impl"

#: The SQL name of the record-fingerprint kernel (identity graph).
FINGERPRINT_UDF_NAME = "golden_fingerprint"

_UDF_CLASS = "dev.goldensuite.spark.GoldenScoreUdf"
_IMPL_UDF_CLASS = "dev.goldensuite.spark.GoldenScoreImplUdf"
_FINGERPRINT_UDF_CLASS = "dev.goldensuite.spark.GoldenFingerprintUdf"

#: The SQL name of the transform chain (normalization).
TRANSFORM_UDF_NAME = "golden_transform"

_TRANSFORM_UDF_CLASS = "dev.goldensuite.spark.GoldenTransformUdf"

#: Transforms the JVM can run, mirroring ``transforms_core::supports``.
#:
#: The RUST side is authoritative -- it is what actually runs -- and this set
#: exists so a chain is refused on the DRIVER, at plan time, with the offending
#: name in hand. Discovering it mid-job, after the plan is distributed, is
#: strictly worse. ``bloom_filter`` (HMAC-keyed PPRL) and plugin transforms are
#: absent on purpose, not pending.
JVM_TRANSFORMS = frozenset({
    "lowercase", "uppercase", "strip", "strip_all", "soundex", "metaphone",
    "digits_only", "alpha_only", "normalize_whitespace", "token_sort",
    "first_token", "last_token", "strip_honorifics",
})


def _is_int(s: str) -> bool:
    try:
        int(s)
    except ValueError:
        return False
    return True


def jvm_supports_transform(name: str) -> bool:
    """Whether the JVM can run one transform. Mirrors ``transforms_core::supports``."""
    if name in JVM_TRANSFORMS:
        return True
    parts = name.split(":")
    if name.startswith("substring:"):
        return len(parts) == 3 and all(_is_int(p) for p in parts[1:])
    if name.startswith("qgram:"):
        return len(parts) == 2 and _is_int(parts[1]) and int(parts[1]) > 0
    return False


def unsupported_transforms(chain: list[str]) -> list[str]:
    """The transforms in ``chain`` the JVM cannot run. Empty means the whole
    chain normalizes with no Python on the executors."""
    return [t for t in chain if not jvm_supports_transform(t)]

#: What ``implementation()`` reports when the Rust kernel loaded.
NATIVE_IMPL = "NativeScorer"

#: ...and when it did not, and the jar fell back to `exact`-only.
FALLBACK_IMPL = "ExactScorer"

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

    Its own type so callers can fall back to the in-Python UDF path rather than
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

    for sql_name, cls, ret in (
        (name, _UDF_CLASS, "array<double>"),
        # Registered alongside, not on demand: the probe has to be available
        # BEFORE anything is scored, or the first thing a caller can check is
        # whether the results they already trusted came from the kernel.
        (IMPL_UDF_NAME, _IMPL_UDF_CLASS, "string"),
        # The identity graph's fingerprint, over the SAME `fingerprint-core`
        # Python uses. Registered here so one `install` call gives a session
        # every capability the jar has -- a caller should not have to know which
        # kernels exist to get them.
        (FINGERPRINT_UDF_NAME, _FINGERPRINT_UDF_CLASS, "string"),
        # The transform chain. Normalization was Python-only until
        # `transforms-core`; this is what lets a cluster normalize a
        # column without an executor virtualenv.
        (TRANSFORM_UDF_NAME, _TRANSFORM_UDF_CLASS, "string"),
    ):
        try:
            spark.udf.registerJavaFunction(sql_name, cls, ret)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            raise JvmScorerUnavailable(
                f"could not register {cls} as {sql_name!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    logger.info("Spark JVM scorer: shipped %s and registered %r", path, name)
    return name


def implementation(spark: object) -> tuple[str, str, str]:
    """Ask an **executor** which scorer it resolved, why, and on what.

    Returns ``(implementation_name, diagnostics, runtime)`` -- see
    :data:`NATIVE_IMPL` and :data:`FALLBACK_IMPL`. ``runtime`` carries the
    executor JVM's version, **heap ceiling** and CPU count.

    The heap is there because the batched scoring path materialises each group as
    an array in JVM heap, so its ceiling is a property of the executor rather
    than of the algorithm -- and a Connect client has no ``SparkContext`` to ask.
    A benchmark that OOMs at an unknown heap size has measured a configuration,
    not a design; this arc has already produced one.

    The jar falls back to the `exact`-only scorer when the native library will
    not load, which keeps a distributed job alive but is otherwise invisible: the
    query still returns numbers, from a narrower path, and every version string
    still looks right. This project has already lost time to that shape of
    failure, so the resolution is queryable and the lane asserts on it.

    **The answer must come from an executor, not the driver.** A driver that
    loads the library says nothing about a cluster whose executors cannot -- and
    a zero-argument deterministic UDF would be constant-folded onto the driver at
    planning time, answering the wrong question perfectly. Hence the probe takes
    a column and this passes it one.
    """
    from pyspark.sql import functions as F

    df = spark.range(1)  # type: ignore[attr-defined]
    raw = df.select(
        F.call_udf(IMPL_UDF_NAME, F.col("id").cast("int")).alias("impl")
    ).collect()[0]["impl"]
    # Fixed-width split, padded: an older jar returns two fields, and unpacking
    # that into three would raise a ValueError that reads as "the probe is
    # broken" rather than "the jar predates the runtime field".
    parts = (str(raw).split("|", 2) + ["", ""])[:3]
    return parts[0], parts[1], parts[2]


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
