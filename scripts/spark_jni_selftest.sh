#!/usr/bin/env bash
# Compile the Spark-FREE half of packages/jvm/goldenmatch-spark and run
# NativeSelfTest against a freshly built kernel -- 10,000 pairs across the JNI
# boundary, on whatever architecture this is running on.
#
# ## Why Spark-free, and why a script
#
# `GoldenScoreUdf` and friends implement `org.apache.spark.sql.api.java.UDF*`,
# so compiling them needs a Spark classpath. The classes that decide whether the
# native kernel loads do not, and keeping them Spark-free is deliberate: the
# piece that must never go untested would otherwise be the piece hardest to
# test. That split means every caller needs the same explicit source list, and
# it now has three: `ci.yml`'s aarch64 lane, the publish workflow's aarch64 job,
# and anyone reproducing either locally.
#
# The list drifts silently in the worst possible way. Adding a class that
# ScorerSelection references and forgetting one caller does not produce a
# missing test -- it produces a COMPILE failure in a lane nobody associates with
# the change. (Adding JarVersion did exactly that.)
#
# ## Inputs (environment)
#
#   ARCH   resource dir for the library: linux-x86-64 | linux-aarch64
#          (default: inferred from `uname -m`)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JVM="$ROOT/packages/jvm/goldenmatch-spark"

if [ -z "${ARCH:-}" ]; then
  case "$(uname -m)" in
    x86_64)          ARCH=linux-x86-64 ;;
    aarch64|arm64)   ARCH=linux-aarch64 ;;
    *) echo "::error::no jar resource dir for machine $(uname -m)"; exit 1 ;;
  esac
fi

# Every source that does NOT need Spark on the classpath. Keep in sync with the
# javac invocation in build_spark_jar.sh, which compiles all of them plus the
# UDF wrappers.
SPARK_FREE=(
  GoldenScorer
  ExactScorer
  SeqCoercion
  Utf8Batch
  JarVersion
  Main
  NativeLibrary
  NativeScorer
  NativeFingerprint
  NativeTransform
  NativeSurvivorship
  ScorerSelection
)

SRC=()
for c in "${SPARK_FREE[@]}"; do
  f="$JVM/src/dev/goldensuite/spark/$c.java"
  [ -f "$f" ] || { echo "::error::$f does not exist; the Spark-free source list is stale"; exit 1; }
  SRC+=("$f")
done

rm -rf "$JVM/build/classes" "$JVM/build/test-classes"
mkdir -p "$JVM/build/classes/native/$ARCH" "$JVM/build/test-classes"

javac --release 17 -encoding UTF-8 -d "$JVM/build/classes" "${SRC[@]}"

SO="$ROOT/packages/rust/extensions/score-jni/target/release/libgoldenmatch_score_jni.so"
[ -f "$SO" ] || { echo "::error::$SO not built"; exit 1; }
cp "$SO" "$JVM/build/classes/native/$ARCH/"

javac --release 17 -encoding UTF-8 -d "$JVM/build/test-classes" \
  -cp "$JVM/build/classes" \
  "$JVM/test/dev/goldensuite/spark/NativeSelfTest.java"

java -cp "$JVM/build/classes:$JVM/build/test-classes" dev.goldensuite.spark.NativeSelfTest

# The same preflight an operator runs, on this architecture, against loose
# classes. It exits non-zero on a fallback, which is the failure that must never
# reach a release. (Version reads "unknown" here -- there is no jar and so no
# manifest; the assembled-jar check in ci.yml covers that half.)
java -cp "$JVM/build/classes" dev.goldensuite.spark.Main
