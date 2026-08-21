package dev.goldensuite.spark;

import java.nio.charset.StandardCharsets;

/** A batch of strings flattened into Arrow's string layout, for the native call.
 *
 * <h2>Why flatten at all</h2>
 *
 * The obvious JNI signature passes {@code String[]} and calls
 * {@code GetStringUTFChars} on each element -- a JNI transition and usually a
 * copy <b>per string</b>. At this arc's batch size (10,000 pairs) that is 20,000
 * JNI calls to avoid one, which undoes the reason the plan was reshaped into
 * batches in the first place.
 *
 * <p>So the batch is packed here into the layout {@code score-cabi} already
 * takes: an {@code int[]} of {@code n + 1} byte offsets plus a {@code byte[]} of
 * packed UTF-8. The native side then pins five primitive arrays -- a fixed cost
 * per <i>batch</i>, not per pair.
 *
 * <h2>Nulls live in the mask, not the data</h2>
 *
 * Arrow's string layout has nowhere to put "absent": a null and an empty string
 * are both a zero-length slice. So {@link #present} carries that distinction and
 * the caller reinstates it after scoring.
 *
 * <p>This matters more than it looks. Letting a missing value read as
 * {@code ""} makes null-vs-null score a perfect 1.0, and two records whose only
 * shared evidence is a shared <i>absence</i> then merge at every threshold. That
 * is not hypothetical -- it is a bug this tier already shipped and fixed.
 *
 * <h2>Spark-free</h2>
 *
 * No Spark type appears here, so the piece most likely to be subtly wrong is
 * unit-testable with no Spark on the classpath. Every failure mode in an encoder
 * produces a <i>number</i> rather than an exception, and a wrong similarity
 * score looks exactly like a right one.
 */
public final class Utf8Batch {

  /** Byte offsets, {@code length + 1} entries, non-decreasing, ending at
   * {@code data.length}. Slot {@code i} is
   * {@code data[offsets[i] .. offsets[i + 1])}. */
  public final int[] offsets;

  /** Packed UTF-8 for every slot, concatenated. */
  public final byte[] data;

  /** {@code false} where the input value was null. A zero-length slice alone
   * cannot say whether the value was absent or empty. */
  public final boolean[] present;

  /** Number of slots. */
  public final int length;

  private Utf8Batch(int[] offsets, byte[] data, boolean[] present, int length) {
    this.offsets = offsets;
    this.data = data;
    this.present = present;
    this.length = length;
  }

  /** Pack {@code values} into Arrow's string layout.
   *
   * <p>Two passes: encode each value once into a per-slot array, then copy into
   * one buffer of the exact total size. The alternative -- a growing buffer --
   * would reallocate and copy repeatedly for a batch whose size is known after
   * the first pass.
   *
   * @param values may contain nulls; must not be null itself
   * @throws IllegalArgumentException if the packed data would exceed
   *     {@link Integer#MAX_VALUE} bytes, which the {@code int} offsets cannot
   *     address -- refused rather than silently overflowing into negative
   *     offsets that {@code score-cabi} would reject with an opaque code from
   *     inside an executor
   */
  public static Utf8Batch encode(String[] values) {
    if (values == null) {
      throw new IllegalArgumentException("values must not be null");
    }
    int n = values.length;
    byte[][] encoded = new byte[n][];
    boolean[] present = new boolean[n];
    long total = 0L;
    for (int i = 0; i < n; i++) {
      String v = values[i];
      if (v == null) {
        // Null slot: zero-length slice, present[i] stays false. It is still
        // SCORED by the kernel (empty vs empty) and the result is discarded by
        // the caller. Compacting nulls out would save that work at the cost of
        // an index remapping -- the one place in this path where an off-by-one
        // would silently attach a score to the wrong pair.
        encoded[i] = EMPTY;
        continue;
      }
      present[i] = true;
      byte[] b = v.getBytes(StandardCharsets.UTF_8);
      encoded[i] = b;
      total += b.length;
    }
    if (total > Integer.MAX_VALUE) {
      throw new IllegalArgumentException(
          "batch packs to " + total + " bytes, which int offsets cannot address;"
              + " reduce the batch size (see batched.DEFAULT_BATCH_SIZE)");
    }

    int[] offsets = new int[n + 1];
    byte[] data = new byte[(int) total];
    int at = 0;
    for (int i = 0; i < n; i++) {
      offsets[i] = at;
      byte[] b = encoded[i];
      System.arraycopy(b, 0, data, at, b.length);
      at += b.length;
    }
    offsets[n] = at;
    return new Utf8Batch(offsets, data, present, n);
  }

  private static final byte[] EMPTY = new byte[0];
}
