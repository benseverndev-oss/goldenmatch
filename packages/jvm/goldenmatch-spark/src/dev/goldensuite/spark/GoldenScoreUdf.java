package dev.goldensuite.spark;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.apache.spark.sql.api.java.UDF3;

/** The Spark surface: score a batch of pairs in one UDF call.
 *
 * <p>The ONLY class here that touches Spark, and deliberately thin -- coerce,
 * delegate, box. Everything with a decision in it ({@link SeqCoercion},
 * {@link GoldenScorer}) is Spark-free and unit-tested without a Spark
 * classpath.
 *
 * <p>Registered from the client as:
 * <pre>
 *   spark.udf.registerJavaFunction(
 *       "golden_score_batch",
 *       "dev.goldensuite.spark.GoldenScoreUdf",
 *       "array&lt;double&gt;")
 * </pre>
 * after {@code spark.addArtifact(jar)}. Both steps are proven over Spark
 * Connect (probe run 31611464914), which is what makes this a drop-in: the
 * customer's cluster needs nothing installed.
 *
 * <p>Arguments are {@code Object} because Spark's Java type for an
 * {@code array<string>} column is version-dependent -- see {@link SeqCoercion}.
 * The scorer id arrives as an argument rather than being baked into the class so
 * one registration serves every scorer; J2 will dispatch it into score-cabi.
 */
public final class GoldenScoreUdf implements UDF3<Integer, Object, Object, List<Double>> {

  /** Names the implementation actually in use, so a caller can tell a J0 jar
   * from a J2 one without reading version strings. Callable from SQL via
   * {@link GoldenScoreImplUdf}, which is how the Python side asserts the jar it
   * shipped is the jar that loaded -- and, since a UDF runs on an EXECUTOR, that
   * the library loaded there rather than merely on the driver.
   *
   * <p>Delegates to {@link ScorerSelection} rather than holding its own static:
   * two sources for "which scorer is running" could disagree, and the one Spark
   * calls would not be the one the tests read. */
  public static String implementationName() {
    return ScorerSelection.implementationName();
  }

  /** Why the native library did or did not load, verbatim. Paired with
   * {@link #implementationName()} so a fallback reports its own cause instead of
   * leaving it in an executor log nobody collects. */
  public static String implementationDiagnostics() {
    return ScorerSelection.diagnostics();
  }

  @Override
  public List<Double> call(Integer scorerId, Object a, Object b) {
    if (scorerId == null) {
      throw new IllegalArgumentException("scorerId must not be null");
    }
    String[] as = SeqCoercion.toStringArray(a);
    String[] bs = SeqCoercion.toStringArray(b);
    if (as == null || bs == null) {
      return null;
    }
    Double[] scores = ScorerSelection.scorer().score(scorerId, as, bs);
    // A fixed-size list from Arrays.asList is fine as a UDF return; Spark reads
    // it and does not mutate. Copying it would allocate a second list per batch
    // for no benefit.
    return new ArrayList<>(Arrays.asList(scores));
  }
}
