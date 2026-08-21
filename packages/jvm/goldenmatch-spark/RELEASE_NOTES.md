The Golden Suite Spark JVM jar: four Rust kernels (scoring, fingerprints,
transforms, survivorship) reachable from Spark executors with **no Python
installed on them**.

```python
spark.addArtifact("goldenmatch-spark.jar")
```

Carries the native library for `linux-x86-64` and `linux-aarch64`. Both were
built and self-tested on their own architecture before this was published; the
aarch64 library is built on real ARM hardware, not cross-compiled.

**Check a jar before submitting anything.** `java -jar goldenmatch-spark.jar`
reports its version and whether the native kernel loads on that machine, and
exits non-zero if it fell back to the `exact`-only scorer. That fallback is
deliberate (a distributed job must not die because one executor could not
`dlopen` a library) and therefore invisible, which is why it is worth checking
explicitly, on the architecture you actually run.

This is a deployment story, not a throughput one: the JVM path measured about
2.4x slower than the Python-worker path on the same workload. What it removes is
the packed virtualenv every executor otherwise needs.

See `packages/jvm/goldenmatch-spark/README.md` for UDF registration, what each
kernel refuses and why, and what still needs a Python worker.
