# JVM-native scoring — running the Rust kernel inside the Spark executor

**Status:** proposed (2026-08-12, Ben) • **Follows** `2026-08-10-spark-native-execution-design.md` (P0–P6)

**Goal:** score inside the executor JVM by calling the Rust kernel directly,
removing the forked Python worker from the hot path.

---

## 1. Why, and why now

The tier reaches Rust through `pandas_udf`: Spark serialises an Arrow batch to a
**forked Python worker**, which calls the kernel via pyo3 and sends results back.
That costs an IPC hop and an interpreter per batch, and it is the sole reason P1
exists at all — `addArtifact` + `venv-pack` ship a Python environment to
executors purely to feed that worker.

Executors are JVMs. A JVM can call the kernel directly.

This is also the repo thesis applied once more, which is the decisive argument
rather than a performance one: `score-core` is pyo3-free and already backs the
Python extension, the DataFusion FFI UDFs and the WASM build. A JVM binding is
**the next host binding over one kernel**, not a fourth implementation.

---

## 2. What is already proven

Not assumed — measured, in CI, before any of this was designed.

| | evidence |
|---|---|
| A C ABI over the kernel | `score-cabi`, 9 tests, in the blocking `rust` lane (#2502) |
| C ↔ Python parity | 4000 pairs × 99 thresholds: `jaro_winkler` and `levenshtein` **bit-exact**, `token_sort` 1.11e-16 (the Python `/100`, not the kernel), **0 threshold flips** |
| Jars reach a Connect session | `addArtifact(jar)` PASS (run 31609185942) |
| Java UDFs register over Connect | `registerJavaFunction` PASS — registered *and called* |
| Batching works | array-shaped UDF, **10,000 pairs in one call** (run 31611464914) |
| The Java arg type | `scala.collection.immutable.ArraySeq$ofRef` — a **Scala** Seq, not `java.util.List` |

The last row is why the probe existed. `java.util.List` is the obvious
declaration and what Spark's own Java UDF examples suggest; it throws
`ClassCastException`, which reads as *"the array shape does not work"* when the
shape is fine and only the declaration was wrong.

---

## 3. Decision: JNI, not FFM

Spark's docs: *"Supported Java versions include 17, 21, and 25."* Panama's FFM
API was **finalised in JDK 22**. So across Spark's supported set:

| JDK | FFM |
|---|---|
| 17 | unavailable |
| 21 | preview only (`--enable-preview`, a non-starter on a customer cluster) |
| 25 | available |

FFM is the nicer API and the better long-term target, but choosing it first
would exclude the JDKs most production clusters actually run. **JNI works on all
three.**

So: a Java interface (`GoldenScorer`) with a JNI implementation now, and room for
an FFM implementation later on JDK 25+ behind the same interface. Nothing is
foreclosed; the fast path can be added without moving the callers.

### And no new build system

The repo has no Maven, sbt or Gradle, and this does not add one. The probe
already builds a jar with plain `javac` + `jar` against the installed pyspark's
own jars, in CI, in about a second. A single small jar does not justify a fourth
toolchain — and compiling against the *installed* Spark keeps compile and
runtime on one version, which is its own class of bug avoided.

---

## 4. Architecture

```
Spark executor JVM
  UDF2<ArraySeq, ArraySeq, List<Double>>     proven; batches to 10k+
      -> Object[] backing array               ofRef wraps one; no Scala iteration
      -> pack: int32 offsets + UTF-8 bytes    ONE copy, into a direct ByteBuffer
      -> JNI downcall                         one per BATCH, not per row
           |
           v
  goldenmatch_score_pairwise_utf8()          score-cabi (C ABI)
      -> score_one()                          score-core, the same kernel Python calls
```

### The copy is real and is not hidden

The array path amortises the **call**; it still copies the strings. Zero-copy
would need Arrow buffers, and nothing on this path produces one: Arrow Java
exists and is on Spark's classpath, but the entry points that expose it
(`ColumnarBatch`, `ArrowColumnVector`) sit behind Catalyst, which Connect
deliberately does not expose — the same wall that rules out a custom
`Expression`.

Whether the copy matters is a measurement, not an argument. Build the copying
version, measure it, and chase zero-copy only if the copy shows up.

---

## 5. The real risk: reshaping the plan

Everything above is proven. This is not, and it is the largest piece of work.

`score_and_dedup` scores **per row**: a block self-join produces `(a, b)` pairs
and a scalar UDF scores each. An array UDF needs pairs **grouped into arrays**,
scored, then flattened back to rows — a genuine plan change:

- group candidate pairs (by block? by a synthetic batch id?) into `array<string>`
  columns
- call the array UDF once per group
- `posexplode` the result and re-align scores to their pairs

**Alignment is the hazard.** `explode` does not promise to preserve a
relationship between two independently exploded arrays; the score must be
carried back to the *right* pair. `posexplode` with an index, or arrays of
structs, are the candidates. A misalignment would not crash — it would silently
score pair *i* with pair *j*'s answer, which is the worst failure mode this
project keeps finding.

That risk sets the plan: the reshape is gated by a parity test against the
existing row-shaped path **before** the native call is introduced, so a
misalignment cannot be confused with a kernel bug.

---

## 6. Phases

**J0 — the jar, with a Java-only kernel.** `GoldenScorer` interface + a pure-Java
implementation. Ship via `addArtifact`, register via `registerJavaFunction`,
prove parity with `core.strsim` on a fixture. No JNI yet: this isolates the
*plumbing* from the *native call*.

**J1 — the plan reshape.** Group → array UDF → explode, still on the pure-Java
scorer. Gated by pair-set parity against the row-shaped path. This is where the
alignment risk lives, and it is deliberately faced without native code in the
picture.

**J2 — JNI.** Swap the Java scorer for a JNI call into `score-cabi`. Parity is
now a *byte-identical* claim against the Python path, which the existing ctypes
harness already knows how to check.

**J3 — packaging.** `.so` inside the jar, extracted per-JVM at load (the
netty/rocksdb pattern), cross-compiled linux x86_64 + aarch64. The repo already
cross-compiles manylinux wheels, so the compile matrix transfers.

**J4 — measurement.** The number this whole arc has never had: wall-clock vs the
`pandas_udf` path on a real workload. Also the §1 differentiator of the parent
spec, still unmeasured.

**J5 — fallback discipline.** If the jar or native library fails to load on an
executor, fall back to the existing `pandas_udf` path. The tier already has this
posture for the native kernel (`_native_scores` returns `None` and the pure floor
runs); a distributed run must not fail because one executor could not `dlopen`.

---

## 7. Non-goals

- **Zero-copy.** Out of reach behind Connect; revisit only if J4 says the copy
  matters.
- **A custom Catalyst `Expression`.** Not registrable from a Connect client.
- **Replacing the Python path.** It stays as the fallback and as the parity
  reference.
- **Scala.** The UDF is Java. Adding Scala would pin the jar to Spark's Scala
  version for no gain.

---

## 8. Exit criteria

1. A Splink-style config runs on Spark with scoring in the executor JVM.
2. Scores are **byte-identical** to the Python path (the standard this project
   already holds itself to; a tolerance is fine for a score you report, not one
   you threshold).
3. A measured wall-clock comparison against `pandas_udf`, published — whatever it
   says.
4. An executor that cannot load the library still produces correct results.
