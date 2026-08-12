package dev.goldensuite.spark;

/** J0's only scorer: {@code exact}, and nothing else.
 *
 * <h2>Why only one, and why this one</h2>
 *
 * J0 exists to prove the PLUMBING -- that a jar reaches a Connect session, that
 * a Java UDF registers, that arrays go in and scores come back aligned -- with
 * no native call in the picture, so a failure in the plan reshape cannot be
 * confused with a failure in the kernel.
 *
 * <p>A pure-Java implementation of jaro-winkler or levenshtein would achieve
 * that too, and would be a mistake: it would be a FOURTH implementation of a
 * kernel that already exists once in Rust and is reached from Python, WASM and
 * DataFusion. This whole arc argues that a score should have one source of
 * truth; writing a Java one to test some plumbing would contradict it, and
 * "temporary" implementations that produce plausible numbers do not stay
 * temporary.
 *
 * <p>{@code exact} escapes that because it is not an algorithm. String equality
 * is identical by inspection in any language -- there is nothing to diverge. It
 * is a real score-core scorer (id 3), so this is a genuine subset of the kernel
 * rather than a stand-in for it.
 *
 * <p>Everything else is refused loudly. J2 replaces this with a JNI call into
 * {@code score-cabi} and the refusals disappear because the kernel arrives.
 */
public final class ExactScorer implements GoldenScorer {

  @Override
  public boolean supports(int scorerId) {
    return scorerId == EXACT;
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
          "ExactScorer runs scorer id " + EXACT + " (exact) only; got " + scorerId
              + ". The JVM path carries no scoring algorithms of its own -- the"
              + " kernel arrives in J2 via JNI into score-cabi. Until then, route"
              + " this scorer through the existing pandas_udf path.");
    }
    Double[] out = new Double[a.length];
    for (int i = 0; i < a.length; i++) {
      String x = a[i];
      String y = b[i];
      // NULL POLICY: not ours. A missing value is absence of evidence, and the
      // caller decides what that means -- the same contract score-cabi's header
      // states, and for the same reason: substituting "" for null made
      // null-vs-null score a perfect 1.0, so two records whose only shared
      // evidence was a shared ABSENCE merged at every threshold.
      out[i] = (x == null || y == null) ? null : (x.equals(y) ? 1.0d : 0.0d);
    }
    return out;
  }
}
