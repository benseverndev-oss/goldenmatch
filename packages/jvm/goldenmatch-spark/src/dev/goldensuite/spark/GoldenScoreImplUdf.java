package dev.goldensuite.spark;

import org.apache.spark.sql.api.java.UDF1;

/** Reports which scorer the EXECUTOR actually resolved, and why.
 *
 * <p>The jar falls back to {@link ExactScorer} when the native library will not
 * load, which keeps a distributed job alive but would otherwise be invisible:
 * the query still returns numbers, just narrower ones from a different code
 * path. This repo has lost real time to exactly that shape of failure (a
 * published wheel silently taking a fallback branch while every version string
 * looked right), so the resolution is queryable and the Spark lane asserts on it.
 *
 * <h2>Why it takes an argument it ignores</h2>
 *
 * A zero-argument deterministic UDF is <b>foldable</b> -- {@code Expression
 * .foldable} is {@code children.forall(_.foldable)}, which is vacuously true
 * with no children -- so Catalyst's constant folding would evaluate it on the
 * <b>driver</b> at planning time and never run it on an executor at all. That
 * would answer the wrong question perfectly: the driver's classloader is not the
 * one that has to find the library.
 *
 * <p>So it takes a column. The argument's value is unused; its presence is what
 * makes the call non-foldable and forces it out to where the answer matters.
 */
public final class GoldenScoreImplUdf implements UDF1<Integer, String> {

  /** @param ignored a column reference, present only to defeat constant folding
   *  @return {@code "<name>|<diagnostics>|<runtime>"} -- one string because a
   *      UDF returns one value, and the three are useless apart: the name says
   *      what ran, the diagnostics say why, and the runtime says on what. The
   *      last is not decoration: the batched scoring path materialises groups in
   *      JVM heap, so a result is uninterpretable without knowing the heap
   *      ceiling, and a Connect client has no other way to ask. */
  @Override
  public String call(Integer ignored) {
    return GoldenScoreUdf.implementationName() + "|"
        + GoldenScoreUdf.implementationDiagnostics() + "|"
        + ScorerSelection.runtimeInfo() + " exec=" + executorId();
  }

  /** Spark's id for the process this ran in: {@code "driver"} in local mode,
   * {@code "0"}, {@code "1"}, ... on a real executor.
   *
   * <h2>Why the jar reports this at all</h2>
   *
   * Because without it, the assertion this class exists to support cannot
   * actually be made. Everything above argues that the driver's classloader is
   * not the one that has to find the library -- and then the whole Spark suite
   * runs under {@code local[*]}, where the executor IS the driver. Spark says so
   * itself: a failed task there is attributed to {@code executor driver}. So
   * "the executor resolved the native kernel" was true and proved nothing, and
   * no amount of care in the UDF could distinguish the two.
   *
   * <p>One string makes the difference observable, and lets the cluster lane
   * assert it is testing something the local lane cannot.
   *
   * <p>Appended to the runtime segment as another {@code key=value} token rather
   * than as a fourth {@code |} field: the client splits on {@code "|"} with a
   * bounded maxsplit, so a new field would be silently glued onto the runtime
   * string. This shape needs no client change and no version negotiation.
   *
   * <p>Spark-specific, so it lives here rather than in {@link ScorerSelection},
   * which is deliberately Spark-free so the load decision can be tested without
   * a Spark classpath. Never throws: this is a diagnostic label, and a job must
   * not die because it could not read one.
   */
  private static String executorId() {
    try {
      org.apache.spark.SparkEnv env = org.apache.spark.SparkEnv.get();
      return env == null ? "?" : env.executorId();
    } catch (Throwable t) {
      return "?";
    }
  }
}
