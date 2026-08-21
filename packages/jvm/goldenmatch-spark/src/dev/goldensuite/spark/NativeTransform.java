package dev.goldensuite.spark;

/** The transform chain (normalization) in the executor JVM.
 *
 * <p>Normalization was Python-only: there was no Rust implementation at all
 * until {@code transforms-core}, which is why {@code config_pipeline._transformed}
 * is an {@code arrow_udf} and why a Spark cluster needed a packed virtualenv to
 * lowercase a column. This is the JVM half of closing that.
 *
 * <h2>The chain is one comma-separated string</h2>
 *
 * Transform names contain {@code :} ({@code substring:0:3}) but never
 * {@code ,}. A {@code String[]} would cost a JNI transition per element to
 * deliver a value that is identical on every row of the query.
 *
 * <h2>Gate the chain, do not discover it</h2>
 *
 * {@code bloom_filter} (HMAC-keyed PPRL) and plugin transforms are Python-only
 * by design. {@link #supportsChain} exists so a caller refuses at PLAN time with
 * the offending name in hand; finding out mid-job, after the plan is already
 * distributed, is strictly worse.
 *
 * <h2>Null means MISSING</h2>
 *
 * Not "empty". {@code strip_honorifics} on an honorific-only name legitimately
 * yields nothing, and Fellegi-Sunter reads an empty string as an agreement on
 * nothing rather than an absence of evidence -- so the distinction is
 * load-bearing all the way down.
 */
public final class NativeTransform {

  private NativeTransform() {}

  /** Apply {@code chain} to {@code value}; {@code null} if either is null or the
   * result is a missing value.
   *
   * @throws IllegalStateException if the native library is unavailable. No
   *     fallback: a value normalized a second way lands in a DIFFERENT BLOCK, so
   *     the pair is never compared and nothing downstream reports a problem.
   */
  public static String apply(String value, String chain) {
    if (value == null || chain == null) {
      return null;
    }
    if (!NativeLibrary.ensureLoaded()) {
      throw new IllegalStateException(
          "the native library is not loaded, so transforms cannot run in the "
              + "JVM: " + NativeLibrary.diagnostics() + ". There is deliberately "
              + "no fallback -- normalization feeds BLOCKING, and a value "
              + "normalized differently is simply never compared.");
    }
    return applyChain(value, chain);
  }

  /** Whether every transform in {@code chain} can run here. */
  public static boolean supports(String chain) {
    return chain != null && NativeLibrary.ensureLoaded() && supportsChain(chain);
  }

  static native String applyChain(String value, String chain);

  static native boolean supportsChain(String chain);
}
