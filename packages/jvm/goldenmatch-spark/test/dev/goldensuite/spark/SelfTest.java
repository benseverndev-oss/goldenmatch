package dev.goldensuite.spark;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Self-test for the Spark-free half of the jar.
 *
 * <p>No JUnit: adding a test framework would mean a dependency resolver, and
 * this package deliberately has no build system (see the J-spec -- one small jar
 * does not justify a fourth toolchain). A {@code main} that exits non-zero is
 * enough for CI to gate on, and it runs anywhere a JDK does.
 *
 * <p>The classes with decisions in them are covered here; only the thin Spark
 * wrapper is left to the lane test.
 */
public final class SelfTest {

  private static int failures = 0;

  public static void main(String[] args) {
    coercionReadsAScalaLikeSeqViaBackingArray();
    coercionReadsAJavaList();
    coercionReadsAnObjectArray();
    coercionPreservesNullElements();
    coercionRefusesAnUnreadableType();
    exactScoresEqualityAndInequality();
    exactReturnsNullForAnUnobservedPair();
    exactRefusesEveryOtherScorer();
    exactRefusesMismatchedLengths();

    if (failures > 0) {
      System.err.println("\n" + failures + " assertion(s) FAILED");
      System.exit(1);
    }
    System.out.println("\nall self-tests passed");
  }

  // ── SeqCoercion ────────────────────────────────────────────────────

  /** Stands in for scala.collection.immutable.ArraySeq$ofRef: exposes both a
   * backing `array()` and size()/apply(int), like the real thing. Written as a
   * fake because depending on scala-library here would pin this test to a Scala
   * version, which is exactly what SeqCoercion avoids. */
  public static final class FakeScalaSeq {
    private final Object[] backing;

    public FakeScalaSeq(Object... backing) {
      this.backing = backing;
    }

    public Object[] array() {
      return backing;
    }

    public int size() {
      return backing.length;
    }

    public Object apply(int i) {
      return backing[i];
    }
  }

  /** Same shape but with NO array() accessor, so the size()/apply() fallback is
   * the path under test rather than dead code. */
  public static final class FakeSeqNoBackingArray {
    private final Object[] backing;

    public FakeSeqNoBackingArray(Object... backing) {
      this.backing = backing;
    }

    public int size() {
      return backing.length;
    }

    public Object apply(int i) {
      return backing[i];
    }
  }

  static void coercionReadsAScalaLikeSeqViaBackingArray() {
    String[] got = SeqCoercion.toStringArray(new FakeScalaSeq("a", "b", "c"));
    eq("scala-like Seq via array()", Arrays.asList("a", "b", "c"), Arrays.asList(got));

    String[] viaApply =
        SeqCoercion.toStringArray(new FakeSeqNoBackingArray("x", "y"));
    eq("scala-like Seq via size()/apply()", Arrays.asList("x", "y"),
        Arrays.asList(viaApply));
  }

  static void coercionReadsAJavaList() {
    List<String> in = new ArrayList<>(Arrays.asList("p", "q"));
    eq("java.util.List", in, Arrays.asList(SeqCoercion.toStringArray(in)));
  }

  static void coercionReadsAnObjectArray() {
    Object[] in = new Object[] {"m", "n"};
    eq("Object[]", Arrays.asList("m", "n"),
        Arrays.asList(SeqCoercion.toStringArray(in)));
  }

  static void coercionPreservesNullElements() {
    // A null ELEMENT is a missing value and must survive coercion; collapsing it
    // to "" is the exact substitution that made null-vs-null score 1.0.
    String[] got = SeqCoercion.toStringArray(new FakeScalaSeq("a", null, "c"));
    eq("null element survives", Arrays.asList("a", null, "c"), Arrays.asList(got));
  }

  static void coercionRefusesAnUnreadableType() {
    try {
      SeqCoercion.toStringArray(new Object());
      fail("unreadable type", "expected IllegalArgumentException, got none");
    } catch (IllegalArgumentException e) {
      pass("unreadable type refused");
    }
  }

  // ── ExactScorer ────────────────────────────────────────────────────

  static void exactScoresEqualityAndInequality() {
    Double[] got = new ExactScorer().score(
        GoldenScorer.EXACT, new String[] {"a", "a"}, new String[] {"a", "b"});
    eq("exact scores", Arrays.asList(1.0d, 0.0d), Arrays.asList(got));
  }

  static void exactReturnsNullForAnUnobservedPair() {
    Double[] got = new ExactScorer().score(
        GoldenScorer.EXACT, new String[] {null, "a", null},
        new String[] {null, null, "b"});
    // null vs null is NOT 1.0. Two records whose only shared evidence is a
    // shared absence must not read as a perfect match.
    eq("unobserved pairs", Arrays.asList(null, null, null), Arrays.asList(got));
  }

  static void exactRefusesEveryOtherScorer() {
    for (int id : new int[] {GoldenScorer.JARO_WINKLER, GoldenScorer.LEVENSHTEIN,
        GoldenScorer.TOKEN_SORT, 99}) {
      try {
        new ExactScorer().score(id, new String[] {"a"}, new String[] {"a"});
        fail("refuse scorer " + id, "expected UnsupportedOperationException");
      } catch (UnsupportedOperationException e) {
        if (!e.getMessage().contains("score-cabi")) {
          fail("refuse scorer " + id, "message should point at the J2 route");
        } else {
          pass("scorer " + id + " refused, and says where the kernel arrives");
        }
      }
    }
  }

  static void exactRefusesMismatchedLengths() {
    try {
      new ExactScorer().score(
          GoldenScorer.EXACT, new String[] {"a"}, new String[] {"a", "b"});
      fail("length mismatch", "expected IllegalArgumentException");
    } catch (IllegalArgumentException e) {
      pass("length mismatch refused");
    }
  }

  // ── tiny assertion helpers ─────────────────────────────────────────

  static void eq(String what, List<?> want, List<?> got) {
    if (want.equals(got)) {
      pass(what);
    } else {
      fail(what, "want " + want + ", got " + got);
    }
  }

  static void pass(String what) {
    System.out.println("  ok   " + what);
  }

  static void fail(String what, String detail) {
    failures++;
    System.out.println("  FAIL " + what + ": " + detail);
  }
}
