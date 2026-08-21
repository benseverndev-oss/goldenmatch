package dev.goldensuite.spark;

/** A batch string scorer, as seen from the executor JVM.
 *
 * <p>Deliberately Spark-free: no Spark type appears in this interface, so the
 * implementations and their tests compile and run without a Spark classpath.
 * Only {@link GoldenScoreUdf} touches Spark, and it is a thin wrapper.
 *
 * <p><b>Batch, not per-pair.</b> The signature takes arrays because Spark
 * Connect only permits row-shaped UDFs (the batch entry points sit behind
 * Catalyst, which Connect does not expose), so the caller groups pairs into
 * arrays to amortise the cost of reaching the implementation. For the JNI
 * implementation in J2 that cost is a downcall; making it per-pair would
 * dominate the work being done.
 *
 * <p><b>Scorer ids are score-core's, not this package's.</b> They must stay in
 * lockstep with {@code score_one} in
 * {@code packages/rust/extensions/score-core/src/lib.rs} and with
 * {@code _NATIVE_SCORER_IDS} on the Python side. An id that means one thing here
 * and another there would not fail loudly -- it would silently score with the
 * wrong function.
 */
public interface GoldenScorer {

  /** score_one's ids. Only the ones this package can name are listed; the
   * kernel owns 0..=14 and the numbering below is a subset, never a re-mapping. */
  int JARO_WINKLER = 0;
  int LEVENSHTEIN = 1;
  int TOKEN_SORT = 2;
  int EXACT = 3;

  /** Score {@code a[i]} against {@code b[i]} for every i.
   *
   * @param scorerId a score-core scorer id (see the constants above)
   * @param a left values; an element may be null
   * @param b right values; an element may be null
   * @return one score per pair, {@code null} where the pair is not comparable
   * @throws IllegalArgumentException if the arrays differ in length
   * @throws UnsupportedOperationException if this implementation cannot run
   *     {@code scorerId} -- refused rather than approximated
   */
  Double[] score(int scorerId, String[] a, String[] b);

  /** Whether {@code scorerId} can be run, so a caller can route rather than
   * catch. */
  boolean supports(int scorerId);
}
