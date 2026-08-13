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
        + ScorerSelection.runtimeInfo();
  }
}
