package dev.goldensuite.spark;

import org.apache.spark.sql.api.java.UDF3;

/** Score ONE pair per call, in the executor JVM.
 *
 * <p>The counterpart to {@link GoldenScoreUdf}, and the experiment J1 never ran.
 *
 * <h2>Why this exists</h2>
 *
 * J1 grouped pairs into arrays because Spark Connect permits only row-shaped
 * UDFs, and it justified that with an assertion: a native downcall per row
 * "would be dominated by call overhead and the kernel would never get to do any
 * work". No number was ever attached to it. J4 then measured the batched path
 * at ~3x SLOWER than the row-shaped Python one, and the plan bisect put ~0.1s
 * on the JNI scoring of 1.9M pairs and <b>+1.4s on {@code arrays_zip}/
 * {@code explode}</b> -- the machinery that exists only to un-batch what was
 * batched.
 *
 * <p>The reason is that Spark's arrays are {@code ArrayData} of
 * {@code InternalRow} objects, not columnar vectors. So batching in the SQL
 * layer materialises every pair three times -- once in the {@code collect_list}
 * struct, once in {@code arrays_zip}, once in {@code explode} -- while the
 * Python path it loses to never leaves the columnar domain at all
 * ({@code make_scorer_udf} is an {@code arrow_udf} over {@code pa.Array}).
 * Batching pays row-wise object churn to avoid a columnar Arrow transfer that
 * is cheaper than the churn.
 *
 * <p>Called per row, this plan has no {@code collect_list}, no
 * {@code arrays_zip} and no {@code explode}: it is shaped exactly like the
 * Python path, differing only in which runtime the scorer call lands in. What
 * it reintroduces is one JNI downcall per pair -- the cost J1 asserted was
 * fatal. That is now a measurable quantity rather than a premise.
 *
 * <p>Both UDFs are registered, and neither is deprecated by the other. Which
 * one wins is a measurement, and it may well depend on the workload: batching
 * amortises string marshalling across a call, which matters more as values get
 * longer, while the per-row shape avoids the reshape entirely, which matters
 * more as the pair count grows.
 *
 * <h2>Same kernel, same ids</h2>
 *
 * Delegates to the same {@link ScorerSelection#scorer()} as the batched UDF, so
 * a score cannot differ between the two paths by construction -- there is one
 * implementation of {@code score_one} and both reach it. The scorer id is an
 * argument for the same reason it is on {@link GoldenScoreUdf}: one
 * registration serves every scorer.
 *
 * <p>Registered from the client as:
 * <pre>
 *   spark.udf.registerJavaFunction(
 *       "golden_score_row",
 *       "dev.goldensuite.spark.GoldenScoreRowUdf",
 *       "double")
 * </pre>
 */
public final class GoldenScoreRowUdf implements UDF3<Integer, String, String, Double> {

  /** @return the score, or {@code null} where the pair is not comparable --
   *      which is what the batched UDF returns for the same input, and what the
   *      Python path's comparability guard produces. A null must NOT become 0.0
   *      here: the kernel maps a missing value to "" and would score
   *      null-vs-null as a perfect 1.0, merging two records whose only shared
   *      evidence is that both are missing the field. */
  @Override
  public Double call(Integer scorerId, String a, String b) {
    if (scorerId == null) {
      throw new IllegalArgumentException("scorerId must not be null");
    }
    // One-element arrays rather than a new single-pair entry point on
    // GoldenScorer. The interface is the contract both implementations satisfy
    // and both are tested against; adding a second method to serve one caller
    // would mean two code paths into the same kernel that could drift. The
    // allocation is two references per call, which is noise beside the JNI
    // transition this exists to measure.
    Double[] scores = ScorerSelection.scorer().score(
        scorerId, new String[] {a}, new String[] {b});
    return scores == null || scores.length == 0 ? null : scores[0];
  }
}
