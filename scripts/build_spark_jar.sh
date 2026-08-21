#!/usr/bin/env bash
# Assemble packages/jvm/goldenmatch-spark into a jar carrying the Rust kernel
# for every supported executor platform.
#
# ## Why this is a script and not two copies of the same YAML
#
# It has two callers that must not drift:
#
#   * `ci.yml`'s `spark_connect` lane, which builds the jar and then TESTS it
#     against a real Spark session;
#   * `publish-goldenmatch-spark-jar.yml`, which builds the jar and SHIPS it.
#
# The lane's own comments argue at length against building the jar twice,
# because a second build is a way for "the jar that was tested" and "the jar
# that shipped" to differ. Publishing makes a second build unavoidable -- a
# release runs on its own tag, and CI artifacts are per-run and expire -- so the
# guard moves down a level: both callers run THIS file, and the release workflow
# re-runs the same self-tests before it publishes anything. The two jars are
# still separate builds, but they cannot be built DIFFERENTLY.
#
# ## Inputs (environment)
#
#   SPARK_JARS   dir of Spark jars for the compile classpath (the two UDF
#                classes implement org.apache.spark.sql.api.java.UDF*)
#   SO_X86_64    linux-x86-64 libgoldenmatch_score_jni.so
#   SO_AARCH64   linux-aarch64 libgoldenmatch_score_jni.so
#   JAR_VERSION  stamped as Implementation-Version (default: 0.0.0-dev)
#   OUT          output jar path (default: <pkg>/build/goldenmatch-spark.jar)
set -euo pipefail

JVM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/packages/jvm/goldenmatch-spark"
: "${SPARK_JARS:?set SPARK_JARS to a directory of Spark jars}"
: "${SO_X86_64:?set SO_X86_64 to the linux-x86-64 .so}"
: "${SO_AARCH64:?set SO_AARCH64 to the linux-aarch64 .so}"
JAR_VERSION="${JAR_VERSION:-0.0.0-dev}"
OUT="${OUT:-$JVM/build/goldenmatch-spark.jar}"

rm -rf "$JVM/build/classes"
mkdir -p "$JVM/build/classes/native/linux-x86-64" \
         "$JVM/build/classes/native/linux-aarch64" \
         "$(dirname "$OUT")"

# `--release 17`: Spark supports Java 17, 21 and 25, and the runner's JDK is only
# one of them. Compiling without it produces a jar that runs here and fails on a
# 17 executor with UnsupportedClassVersionError.
#
# `-encoding UTF-8`: the sources carry multi-byte literals (the parity fixtures)
# and javac otherwise reads them in the platform default, which is not UTF-8
# everywhere.
javac --release 17 -encoding UTF-8 -cp "$SPARK_JARS/*" -d "$JVM/build/classes" \
  "$JVM"/src/dev/goldensuite/spark/*.java

cp "$SO_X86_64" "$JVM/build/classes/native/linux-x86-64/libgoldenmatch_score_jni.so"
cp "$SO_AARCH64" "$JVM/build/classes/native/linux-aarch64/libgoldenmatch_score_jni.so"

# Assert each library IS the architecture its resource path claims. Swapping
# them yields a jar that loads on NEITHER platform -- dlopen fails on every
# executor and both fall back silently to the exact-only scorer.
readelf -h "$JVM/build/classes/native/linux-x86-64/libgoldenmatch_score_jni.so" \
  | grep -q "X86-64" || { echo "::error::the linux-x86-64 resource is not an x86-64 object"; exit 1; }
readelf -h "$JVM/build/classes/native/linux-aarch64/libgoldenmatch_score_jni.so" \
  | grep -q "AArch64" || { echo "::error::the linux-aarch64 resource is not an AArch64 object"; exit 1; }

# Implementation-Version is what JarVersion reports back through
# `golden_score_impl`, so a jar found on a cluster can name its own release.
#
# Main-Class makes `java -jar goldenmatch-spark.jar` a preflight check: it
# reports the version and whether the native kernel loads on THIS machine,
# which is otherwise only discoverable by submitting a job and reading a UDF.
MANIFEST="$JVM/build/manifest.txt"
{
  echo "Implementation-Title: goldenmatch-spark"
  echo "Implementation-Version: $JAR_VERSION"
  echo "Implementation-Vendor: benseverndev-oss/goldenmatch"
  echo "Main-Class: dev.goldensuite.spark.Main"
} > "$MANIFEST"

jar --create --file "$OUT" --manifest "$MANIFEST" -C "$JVM/build/classes" .

# The jar WITHOUT a library still runs -- it falls back to the exact-only scorer
# so a distributed job survives -- which is exactly why an absence has to be an
# error here rather than a surprise on somebody's cluster. BOTH arches: a
# Graviton fleet finding no aarch64 resource is precisely the failure this
# exists to prevent.
for arch in linux-x86-64 linux-aarch64; do
  jar --list --file "$OUT" | grep -qx "native/$arch/libgoldenmatch_score_jni.so" \
    || { echo "::error::no $arch library in the jar; that platform would silently fall back to the J0 scorer"; exit 1; }
done

echo "--- $OUT (version $JAR_VERSION) ---"
jar --list --file "$OUT"
ls -lh "$OUT"
