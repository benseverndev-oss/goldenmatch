package dev.goldensuite.spark;

/** Record fingerprints from the executor JVM, via the same Rust kernel Python
 * uses.
 *
 * <h2>Why this is here</h2>
 *
 * The jar-only inventory measured what a Spark cluster can run with no Python on
 * its executors, and found 3 of 7: pure Spark SQL, clustering, and J2's scoring.
 * The identity graph was not among them -- ``identity.py`` defines five
 * ``arrow_udf``s, and the first of them derives every record's fingerprint. So a
 * cluster still had to be handed a packed virtualenv to compute an ID.
 *
 * <p>It did not need a new algorithm to fix. ``fingerprint-core`` is already
 * pyo3-free and already exposes ``fingerprint_json``, written -- in its own
 * manifest's words -- for "non-Python callers (pgrx / DuckDB / C ABI)". This is
 * one more such caller.
 *
 * <h2>A JSON string, not Arrow buffers</h2>
 *
 * Deliberately unlike {@link NativeScorer}. Scoring is quadratic in block size,
 * so its per-call cost had to be amortised across a batch of pairs; that is the
 * entire reason {@link Utf8Batch} exists. Fingerprinting is once per RECORD --
 * linear, and orders of magnitude fewer calls -- and Spark can build the JSON in
 * the engine with {@code to_json(struct(*))}, no Python anywhere. Inventing an
 * Arrow protocol here would add a marshaling layer to save a cost nobody is
 * paying.
 *
 * <h2>Null means refused, not "empty record"</h2>
 *
 * The native side returns null for invalid JSON, a non-object, or an
 * unsupported value, rather than throwing: a pending JNI exception that a caller
 * forgets to check detonates somewhere unrelated. An empty object is a VALID
 * record with a real fingerprint, so null cannot be confused with it.
 */
public final class NativeFingerprint {

  private NativeFingerprint() {}

  /** Fingerprint a JSON object, or {@code null} if it cannot be read.
   *
   * <p>Drops {@code __}-prefixed keys, matching
   * {@code core._hashing.record_fingerprint} -- the drop happens inside the
   * shared kernel, so the two cannot disagree about which fields count.
   *
   * @throws IllegalStateException if the native library is unavailable. Unlike
   *     the scorer there is no fallback: a WRONG fingerprint silently splits or
   *     merges identities, so refusing is the only safe failure.
   */
  public static String of(String json) {
    if (json == null) {
      return null;
    }
    if (!NativeLibrary.ensureLoaded()) {
      throw new IllegalStateException(
          "the native library is not loaded, so record fingerprints cannot be "
              + "computed in the JVM: " + NativeLibrary.diagnostics()
              + ". There is deliberately no fallback -- a fingerprint computed a "
              + "second way would silently split or merge identities.");
    }
    return fingerprintJson(json);
  }

  /** Whether fingerprints can be computed here at all. */
  public static boolean available() {
    return NativeLibrary.ensureLoaded();
  }

  static native String fingerprintJson(String json);
}
