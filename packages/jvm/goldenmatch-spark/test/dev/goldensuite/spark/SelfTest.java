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
    utf8EncodesOffsetsAsByteOffsetsNotCharOffsets();
    utf8EncodesAnEmptyBatch();
    utf8DistinguishesNullFromEmptyString();
    utf8OffsetsAreNonDecreasingAndEndAtDataLength();
    utf8RoundTripsEveryValue();
    theResourcePathMatchesWhatTheBuildStages();

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

  // ── Utf8Batch ──────────────────────────────────────────────────────
  //
  // The J2 marshaling layer, and the piece most likely to be silently wrong:
  // every failure mode here produces a NUMBER rather than an exception, and a
  // wrong similarity score looks exactly like a right one.

  static void utf8EncodesOffsetsAsByteOffsetsNotCharOffsets() {
    // The classic Arrow marshaling bug. "Zoë" is 3 chars and 4 UTF-8 bytes; an
    // encoder that writes char counts truncates the value and scores garbage
    // against a correctly-encoded counterpart.
    Utf8Batch b = Utf8Batch.encode(new String[] {"Zoë", "ab"});
    eqInts("byte offsets", new int[] {0, 4, 6}, b.offsets);
    eq("byte count", 6, b.data.length);
  }

  static void utf8EncodesAnEmptyBatch() {
    // An empty batch is a real case (a group can filter to nothing), and the
    // offsets array must still be n+1 = 1 element or the kernel reads past it.
    Utf8Batch b = Utf8Batch.encode(new String[] {});
    eqInts("empty batch offsets", new int[] {0}, b.offsets);
    eq("empty batch data", 0, b.data.length);
    eq("empty batch length", 0, b.length);
  }

  static void utf8DistinguishesNullFromEmptyString() {
    // Both encode to a zero-length slice -- there is nowhere else for a null to
    // go in Arrow's layout -- so the PRESENCE MASK is the only thing that keeps
    // them apart. If it did not, a missing value would score as an empty string
    // and null-vs-null would come back a perfect 1.0: the exact substitution
    // that merged records whose only shared evidence was a shared absence.
    Utf8Batch b = Utf8Batch.encode(new String[] {null, "", "x"});
    eqInts("null and empty both empty slices", new int[] {0, 0, 0, 1}, b.offsets);
    eqBools("presence mask", new boolean[] {false, true, true}, b.present);
  }

  static void utf8OffsetsAreNonDecreasingAndEndAtDataLength() {
    // score-cabi REFUSES decreasing offsets or offsets past the buffer, so a
    // violation here is a hard error rather than a wrong score -- but it would
    // surface as an opaque -3 from inside an executor. Catch it at the source.
    Utf8Batch b = Utf8Batch.encode(new String[] {"alpha", null, "", "béta", "c"});
    boolean ok = b.offsets.length == b.length + 1
        && b.offsets[0] == 0
        && b.offsets[b.length] == b.data.length;
    for (int i = 0; ok && i < b.length; i++) {
      ok = b.offsets[i] <= b.offsets[i + 1];
    }
    if (ok) {
      pass("offsets non-decreasing and terminated at data.length");
    } else {
      fail("offsets invariant", Arrays.toString(b.offsets) + " over " + b.data.length
          + " bytes");
    }
  }

  static void utf8RoundTripsEveryValue() {
    // The end-to-end property: whatever went in must be readable back out of
    // the slices, byte for byte. Anything weaker passes while the encoder is
    // off by one and only shows up as a slightly wrong similarity.
    String[] in = {"alpha", null, "", "béta", "日本語", "z"};
    Utf8Batch b = Utf8Batch.encode(in);
    List<String> got = new ArrayList<>();
    for (int i = 0; i < b.length; i++) {
      if (!b.present[i]) {
        got.add(null);
        continue;
      }
      got.add(new String(b.data, b.offsets[i], b.offsets[i + 1] - b.offsets[i],
          java.nio.charset.StandardCharsets.UTF_8));
    }
    eq("round trip", Arrays.asList(in), got);
  }

  // ── NativeLibrary resource resolution (J3) ─────────────────────────

  static void theResourcePathMatchesWhatTheBuildStages() {
    // The loader computes a resource path from os.name/os.arch; CI stages files
    // at literal paths. Nothing connects the two but this assertion, and a
    // mismatch does not crash -- the resource is simply not found, the jar
    // falls back to the `exact`-only scorer, and a cluster runs slower and
    // narrower without saying so. That is the failure mode J3 exists to
    // prevent, so the two spellings are pinned against each other here.
    //
    // The names are asserted for the platforms CI BUILDS, independently of the
    // platform this test runs on -- otherwise the aarch64 mapping would only
    // ever be checked on aarch64, which is exactly the platform nobody was
    // testing when the jar shipped x86-only.
    eqStr("x86-64 linux resource",
        "/native/linux-x86-64/libgoldenmatch_score_jni.so",
        resourceFor("Linux", "amd64"));
    eqStr("aarch64 linux resource",
        "/native/linux-aarch64/libgoldenmatch_score_jni.so",
        resourceFor("Linux", "aarch64"));
    // A Graviton JVM may report either spelling depending on vendor.
    eqStr("arm64 is the same as aarch64",
        "/native/linux-aarch64/libgoldenmatch_score_jni.so",
        resourceFor("Linux", "arm64"));
    // x86_64 is what some JVMs report instead of amd64.
    eqStr("x86_64 is the same as amd64",
        "/native/linux-x86-64/libgoldenmatch_score_jni.so",
        resourceFor("Linux", "x86_64"));
    // Not built by CI, but the mapping must still be stable -- these are the
    // paths an in-tree developer build stages to.
    eqStr("macOS arm resource",
        "/native/darwin-aarch64/libgoldenmatch_score_jni.dylib",
        resourceFor("Mac OS X", "aarch64"));
    eqStr("windows resource",
        "/native/windows-x86-64/goldenmatch_score_jni.dll",
        resourceFor("Windows 11", "amd64"));
  }

  /** {@link NativeLibrary#resourcePath()} as it would resolve on another
   * platform. The properties are restored afterwards so this cannot leak into a
   * later test -- and into the LOADER, which reads the same properties. */
  static String resourceFor(String osName, String osArch) {
    String oldOs = System.getProperty("os.name");
    String oldArch = System.getProperty("os.arch");
    try {
      System.setProperty("os.name", osName);
      System.setProperty("os.arch", osArch);
      return NativeLibrary.resourcePath();
    } finally {
      System.setProperty("os.name", oldOs);
      System.setProperty("os.arch", oldArch);
    }
  }

  // ── tiny assertion helpers ─────────────────────────────────────────

  static void eqStr(String what, String want, String got) {
    if (want.equals(got)) {
      pass(what);
    } else {
      fail(what, "want " + want + ", got " + got);
    }
  }

  static void eq(String what, int want, int got) {
    if (want == got) {
      pass(what);
    } else {
      fail(what, "want " + want + ", got " + got);
    }
  }

  static void eqInts(String what, int[] want, int[] got) {
    if (Arrays.equals(want, got)) {
      pass(what);
    } else {
      fail(what, "want " + Arrays.toString(want) + ", got " + Arrays.toString(got));
    }
  }

  static void eqBools(String what, boolean[] want, boolean[] got) {
    if (Arrays.equals(want, got)) {
      pass(what);
    } else {
      fail(what, "want " + Arrays.toString(want) + ", got " + Arrays.toString(got));
    }
  }


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
