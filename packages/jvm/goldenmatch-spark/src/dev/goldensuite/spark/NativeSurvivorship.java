package dev.goldensuite.spark;

/** Golden-record survivorship in the executor JVM.
 *
 * <p>There was no pyo3-free Rust implementation until {@code
 * survivorship-core}: {@code merge_field} existed in Rust only inside the pyo3
 * {@code native} crate, and even there as the fused columnar INDICES kernel
 * rather than this per-cluster value merge. So survivorship was Python-only and
 * {@code spark/golden.py} had to be an {@code arrow_udf}.
 *
 * <h2>Values reuse {@link Utf8Batch}</h2>
 *
 * Rather than a {@code String[]} and a {@code GetStringUTFChars} per element.
 * The Arrow marshaling is already written, already tested, and already handles
 * the multi-byte case a per-element loop gets wrong. The presence mask matters
 * on its own: a null MEMBER is not an empty value, and survivorship ignores
 * absent members rather than letting them vote for {@code ""}.
 *
 * <h2>Strategies are gated, not discovered</h2>
 *
 * {@code source_priority} and {@code most_recent} need a sources or dates list
 * that the Spark path does not pass -- Python RAISES for them, and so does the
 * kernel. {@code custom:*} is arbitrary Python. {@link #supports} lets a caller
 * refuse at plan time with the strategy named.
 *
 * <p>The refusals are the point: a survivor chosen by a different rule is a
 * <b>wrong golden record that looks right</b> -- no exception, no null, just a
 * plausible value nothing downstream can flag.
 */
public final class NativeSurvivorship {

  private NativeSurvivorship() {}

  /** The surviving value for one cluster, or {@code null} when there is none.
   *
   * @param values one entry per cluster member; nulls are absent members
   * @param strategy a survivorship strategy name
   * @throws IllegalStateException if the native library is unavailable -- no
   *     fallback, because a golden record built a second way is wrong in a way
   *     that raises nothing
   */
  public static String merge(String[] values, String strategy) {
    if (values == null || strategy == null) {
      return null;
    }
    if (!NativeLibrary.ensureLoaded()) {
      throw new IllegalStateException(
          "the native library is not loaded, so survivorship cannot run in the "
              + "JVM: " + NativeLibrary.diagnostics() + ". There is deliberately "
              + "no fallback -- a survivor chosen a second way is a wrong golden "
              + "record that looks right.");
    }
    Utf8Batch b = Utf8Batch.encode(values);
    return mergeField(b.offsets, b.data, b.present, b.length, strategy);
  }

  /** Whether the loaded kernel can run {@code strategy}. */
  public static boolean supports(String strategy) {
    return strategy != null
        && NativeLibrary.ensureLoaded()
        && supportsStrategy(strategy);
  }

  static native String mergeField(
      int[] offsets, byte[] data, boolean[] present, int n, String strategy);

  static native boolean supportsStrategy(String strategy);
}
