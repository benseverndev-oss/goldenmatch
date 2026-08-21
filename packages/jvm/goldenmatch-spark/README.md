# goldenmatch-spark

The Golden Suite's Rust kernels, reachable from Spark executors with **no Python
installed on them**. One jar, delivered at runtime by `addArtifact`.

```python
spark.addArtifact("goldenmatch-spark.jar")
```

## Why this exists

The Spark tier scores by forking a Python worker per batch. That works, but it
means every executor needs a goldenmatch environment: a virtualenv built for the
executor's platform, packed, shipped, unpacked, and kept in step with the client.
That apparatus is the reason the tier is annoying to deploy.

Executors are JVMs, and the kernels are already pyo3-free Rust with a C ABI. So
the jar calls them directly over JNI, and the library rides inside the jar --
extracted per JVM, no `java.library.path`, nothing installed on the cluster.

**This is not a throughput story.** It was measured, and the JVM path is about
2.4x *slower* than the Python-worker path on the same workload (run
`31656227516`). Scoring is roughly 6% of wall time; the cost is that Spark
Connect only permits row-shaped UDFs, which forces the batching this works
around. The reason to use it is deployment, not speed.

## What's in it

Four kernels, all reaching the same Rust the Python path uses, all in one
library per platform:

| SQL function | Kernel | Rust crate |
| --- | --- | --- |
| `golden_score_batch` | pairwise scoring | `score-cabi` → `score-core` |
| `golden_fingerprint` | record fingerprints (identity graph) | `fingerprint-core` |
| `golden_transform` | normalization chains | `transforms-core` |
| `golden_survivorship` | golden-record survivorship | `survivorship-core` |
| `golden_score_impl` | which scorer *this executor* resolved | — |

Platforms: `linux-x86-64` and `linux-aarch64`. Both are built **and self-tested
on their own architecture** before release; the aarch64 library is built on real
ARM hardware rather than cross-compiled.

## What it unlocks: distributed Fellegi-Sunter training

The kernel table above is the SCORING story, and it is the part this README used
to be entirely about. The bigger consequence is that **Fellegi-Sunter model
training now runs distributed**, on the same jar-only executors.

FS training is an EM loop over comparison vectors. The expensive half is the
E-step, which reads only the comparison vector -- so identical vectors collapse
to one row carrying a count, and the whole E-step becomes a single Spark
`GROUP BY` over agreement patterns. The cluster does the counting; the driver
only fits the model over the collapsed rows.

That makes training cost track the number of DISTINCT comparison vectors,
bounded by `prod(levels + 1)`, instead of the pair count. Measured on a real
two-worker cluster with no Python installed on the executors:

| rows | candidate pairs | distinct patterns | distributed counting | driver-side EM |
| ---: | ---: | ---: | ---: | ---: |
| 1M | 9,838,610 | 433 / 186 | 84.42s | 0.00s |
| 5M | 49,191,275 | 446 / 191 | 443.32s | 0.01s |

Pairs grew **5.00x** and the counting stage **5.25x** (near-linear -- that is the
work a cluster exists to absorb), while distinct patterns grew **3.0%** and the
driver's EM stayed at the timer floor. 446 against a ceiling of 1,024 for that
config, with five times the data.

The measurable claim is the pattern count, not the wall clock: two unmeasurably
small training times would prove nothing, whereas patterns staying flat while
pairs quintuple is the collapse actually holding.

Driven from Python (`goldenmatch.spark`); the executors run the jar. For what
the executors do and do not still need, read the measured inventory rather
than this section -- see [What still needs Python on the
executors](#what-still-needs-python-on-the-executors), which exists because
"no Python on the executors" was a claim before it was a measurement.

## Get it

Download `goldenmatch-spark.jar` from a
[`goldenmatch-spark-v*` release](https://github.com/benseverndev-oss/goldenmatch/releases?q=goldenmatch-spark).
Each release carries a `.sha256` beside it.

```bash
gh release download goldenmatch-spark-v1.0.0 \
  --repo benseverndev-oss/goldenmatch --pattern 'goldenmatch-spark.jar*'
sha256sum -c goldenmatch-spark.jar.sha256
```

## Check it before you submit anything

The jar is runnable, and this is worth doing once on a box that resembles an
executor:

```console
$ java -jar goldenmatch-spark.jar
goldenmatch-spark 1.0.0
  runtime:        java=17.0.13 heap_max=4096MB cpus=8 jar=1.0.0
  os.arch:        amd64
  scorer:         NativeScorer  (native kernel)
  library:        extracted native/linux-x86-64/libgoldenmatch_score_jni.so, loaded OK
```

**Exit code 0 means the native kernel loaded. Exit code 1 means it did not**, and
the output says why.

That distinction matters more than it looks. When the library will not load, the
jar falls back to an `exact`-only scorer rather than throwing -- a distributed
job must not die because one executor could not `dlopen` a shared library. The
cost of that choice is that a broken deployment looks exactly like a working one:
the query still returns numbers, from a narrower path, and every version string
still reads correctly. So check explicitly, and check on the architecture you
actually run.

## Use it

### From Python (recommended)

```python
from pyspark.sql import SparkSession
from goldenmatch.spark.jvm import install, implementation

spark = SparkSession.builder.remote("sc://your-cluster:15002").getOrCreate()

install(spark, jar="goldenmatch-spark.jar")   # ships it, registers all five UDFs

impl, diagnostics, runtime = implementation(spark)
assert impl == "NativeScorer", f"executors fell back: {diagnostics}"
```

`implementation()` asks an **executor**, not the driver. A driver that loads the
library says nothing about a cluster whose executors cannot.

### By hand

```python
spark.addArtifact("goldenmatch-spark.jar")
spark.udf.registerJavaFunction(
    "golden_score_batch", "dev.goldensuite.spark.GoldenScoreUdf", "array<double>")
spark.udf.registerJavaFunction(
    "golden_transform", "dev.goldensuite.spark.GoldenTransformUdf", "string")
spark.udf.registerJavaFunction(
    "golden_survivorship", "dev.goldensuite.spark.GoldenSurvivorshipUdf", "string")
spark.udf.registerJavaFunction(
    "golden_fingerprint", "dev.goldensuite.spark.GoldenFingerprintUdf", "string")
spark.udf.registerJavaFunction(
    "golden_score_impl", "dev.goldensuite.spark.GoldenScoreImplUdf", "string")
```

### Spark Connect only

`addArtifact` is a Spark **Connect** capability. It raises on a classic session.
Build the session with `.remote(...)`. On classic Spark, put the jar on the
executor classpath yourself (`--jars`) and skip `addArtifact`; the UDF
registration is the same.

## What it refuses, and why

Each kernel refuses work it cannot do *identically* to the Python path -- on the
driver, at plan time, naming the offender:

| Kernel | Refuses |
| --- | --- |
| `golden_transform` | `bloom_filter` (HMAC-keyed PPRL) and plugin transforms |
| `golden_survivorship` | `source_priority`, `most_recent`, `custom:*` |
| `golden_fingerprint` | columns whose types are not proven-safe to encode |

None of these fail loudly on their own if allowed through. A wrong fingerprint
splits an identity. A differently-normalized value lands in a different block and
is never compared. A wrong survivor is a golden record that simply looks right.
So there is no fallback on any of the three -- unlike the scorer, where falling
back costs speed and nothing else.

Check a chain before you submit:

```python
from goldenmatch.spark.jvm import unsupported_transforms

bad = unsupported_transforms(["lowercase", "bloom_filter"])
# ['bloom_filter'] -- this chain needs a Python worker
```

## What still needs Python on the executors

`scripts/spark_jar_only_inventory.py` measures this on every CI run, against a
deliberately **empty** executor environment, and writes the answer to the job
summary. Read that rather than trusting this paragraph: the point of the
inventory is that "no Python on the executors" was a claim before it was a
measurement, and it was only partly true.

## Build it yourself

No build system on purpose -- one small jar does not justify a fourth toolchain.

```bash
cargo build --release --manifest-path packages/rust/extensions/score-jni/Cargo.toml

SPARK_JARS="$(python -c 'import pyspark,os; print(os.path.join(os.path.dirname(pyspark.__file__),"jars"))')" \
SO_X86_64=packages/rust/extensions/score-jni/target/release/libgoldenmatch_score_jni.so \
SO_AARCH64=/path/to/aarch64/libgoldenmatch_score_jni.so \
JAR_VERSION=0.0.0-dev \
  bash scripts/build_spark_jar.sh
```

Both architectures are required: the build refuses to produce a jar missing
either, because a Graviton fleet finding no `linux-aarch64` resource would fall
back silently, which is the exact failure this is built to prevent.

## Release

Push a `goldenmatch-spark-vX.Y.Z` tag. `publish-goldenmatch-spark-jar.yml` builds
both architectures, self-tests each on its own hardware, runs the assembled jar,
then creates the release and attaches the jar.

Do **not** `gh release create` the tag first. This repo has immutable releases
enabled, which seals assets at publish time -- a pre-created release makes the
attach step fail, and a tag consumed by an immutable release is burned
permanently (`gh release delete --cleanup-tag` does not free it). The workflow
creates the release as a draft, attaches, then publishes. Let it own that.

To exercise the whole pipeline without spending a tag, dispatch it with
`dry_run: true`: it builds, tests, and uploads the jar as a run artifact,
publishing nothing.
