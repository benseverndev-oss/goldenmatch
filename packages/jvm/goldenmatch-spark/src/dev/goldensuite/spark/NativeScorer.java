package dev.goldensuite.spark;

/** J2: the Rust kernel, called from inside the executor JVM.
 *
 * <h2>What this replaces</h2>
 *
 * {@link ExactScorer} carried one scorer because J0 deliberately shipped no
 * algorithms: a Java jaro-winkler would have been a fourth implementation of a
 * kernel that already exists once in Rust, and this project's position is that a
 * score should have one source of truth. This class keeps that position and
 * removes the restriction by <i>calling</i> the kernel instead of copying it --
 * the same {@code score_one} that {@code native} (pyo3), {@code datafusion-udf},
 * {@code score-wasm} and {@code score-cabi} all wrap.
 *
 * <h2>What it does NOT change</h2>
 *
 * Null policy stays here, in the host. {@code score-cabi} has no validity
 * bitmap by design, and {@link Utf8Batch} keeps the presence mask, so an
 * unobserved pair returns {@code null} rather than being scored against an
 * empty string. Null slots are still handed to the kernel (as zero-length
 * slices) and their results discarded -- wasted work, chosen over an index
 * remapping that could silently attach a score to the wrong pair.
 *
 * <h2>Skew is refused, not absorbed</h2>
 *
 * The library reports its ABI version and how many scorer ids it dispatches,
 * and both are checked before this class will run anything. A caller and a
 * kernel that disagree do not fail loudly on their own: an unknown id falls to
 * {@code score_one}'s catch-all and returns a confident {@code 0.0}. This repo
 * has already paid for a version skew that produced plausible numbers, so the
 * gate is at load time.
 */
public final class NativeScorer implements GoldenScorer {

  /** The ABI this class was written against. Bumped in lockstep with
   * {@code goldenmatch_score_abi_version} in score-cabi. */
  static final int EXPECTED_ABI = 1;

  private final int scorerIdCount;

  private NativeScorer(int scorerIdCount) {
    this.scorerIdCount = scorerIdCount;
  }

  /** Load the library, verify it, and return a usable scorer -- or {@code null}
   * if any of that fails.
   *
   * <p>Null rather than an exception because the caller's correct response is to
   * fall back, not to abort a distributed job. The reason is left in
   * {@link NativeLibrary#diagnostics()}, which the Spark lane asserts on so a
   * fallback cannot pass for a native run.
   */
  public static NativeScorer createOrNull() {
    if (!NativeLibrary.ensureLoaded()) {
      return null;
    }
    try {
      int abi = abiVersion();
      if (abi != EXPECTED_ABI) {
        NativeLibrary.recordUnusable(
            "ABI mismatch: library reports " + abi + ", this jar expects "
                + EXPECTED_ABI + ". Rebuild the jar and the library together.");
        return null;
      }
      int count = scorerIdCount();
      if (count <= 0) {
        NativeLibrary.recordUnusable("library reports " + count + " scorer ids");
        return null;
      }
      return new NativeScorer(count);
    } catch (Throwable t) {
      // UnsatisfiedLinkError if the symbols are absent -- a library that loaded
      // but is not the one we think it is.
      NativeLibrary.recordUnusable(
          "loaded but unusable: " + t.getClass().getSimpleName() + ": " + t.getMessage());
      return null;
    }
  }

  /** How many scorer ids the loaded kernel dispatches. */
  public int scorerIds() {
    return scorerIdCount;
  }

  @Override
  public boolean supports(int scorerId) {
    return scorerId >= 0 && scorerId < scorerIdCount;
  }

  @Override
  public Double[] score(int scorerId, String[] a, String[] b) {
    if (a == null || b == null) {
      throw new IllegalArgumentException("null input array");
    }
    if (a.length != b.length) {
      throw new IllegalArgumentException(
          "array length mismatch: " + a.length + " vs " + b.length);
    }
    if (!supports(scorerId)) {
      throw new UnsupportedOperationException(
          "scorer id " + scorerId + " is outside the loaded kernel's 0.."
              + (scorerIdCount - 1) + ". An id the kernel does not know would be"
              + " scored by its catch-all arm as 0.0, so it is refused here.");
    }

    Utf8Batch left = Utf8Batch.encode(a);
    Utf8Batch right = Utf8Batch.encode(b);
    double[] raw = new double[a.length];
    int rc = scorePairwiseUtf8(
        scorerId, left.offsets, left.data, right.offsets, right.data, a.length, raw);
    if (rc != 0) {
      throw new IllegalStateException(
          "native scoring failed with code " + rc + " (" + describe(rc) + ")"
              + " for scorer " + scorerId + " over " + a.length + " pairs");
    }

    Double[] out = new Double[a.length];
    for (int i = 0; i < out.length; i++) {
      // Reinstate absence. The kernel scored the null slots as empty-vs-empty
      // and those numbers are meaningless -- keeping them is how null-vs-null
      // becomes a perfect 1.0.
      out[i] = (left.present[i] && right.present[i]) ? Double.valueOf(raw[i]) : null;
    }
    return out;
  }

  /** Error codes from the native layer, kept in one place on this side too.
   *
   * <p>Mirrors {@code score_jni::describe}. The duplication is deliberate: the
   * message has to be readable from a Java stack trace with no Rust in sight,
   * and a code the JVM cannot name is a code nobody diagnoses.
   */
  static String describe(int code) {
    switch (code) {
      case 0:
        return "ok";
      case -1:
        return "a required buffer pointer was NULL";
      case -2:
        return "batch length was negative or unrepresentable";
      case -3:
        return "offsets were decreasing, negative, or ran past the data buffer";
      case -4:
        return "a slice was not valid UTF-8";
      case -10:
        return "an array was too short for the batch length";
      case -11:
        return "scorer id outside 0..=255";
      case -12:
        return "a JNI array could not be pinned";
      default:
        return "unknown error code";
    }
  }

  // ── native entry points (score-jni/src/lib.rs) ──────────────────────
  //
  // Declaring these does NOT trigger loading; an absent library surfaces as
  // UnsatisfiedLinkError on the first CALL, which is why createOrNull() gates
  // every use behind NativeLibrary.ensureLoaded().

  static native int abiVersion();

  static native int scorerIdCount();

  /** Score {@code n} pairs from Arrow-layout buffers into {@code out}.
   *
   * @return 0, or a negative code -- see {@link #describe}. Errors are codes
   *     rather than exceptions because a pending JNI exception that a caller
   *     forgets to check detonates somewhere unrelated.
   */
  static native int scorePairwiseUtf8(
      int scorerId,
      int[] aOffsets,
      byte[] aData,
      int[] bOffsets,
      byte[] bData,
      int n,
      double[] out);
}
