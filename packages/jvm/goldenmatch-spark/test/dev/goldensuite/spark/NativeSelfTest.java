package dev.goldensuite.spark;

import java.util.Arrays;
import java.util.List;

/** End-to-end gate for the JNI path, with no Spark anywhere.
 *
 * <p>Separate from {@link SelfTest} because it REQUIRES the native library:
 * SelfTest must stay runnable on any JDK with nothing built, and folding these
 * in would either make it fail without a library or teach it to skip -- and a
 * test that skips silently is how a lane goes green while testing nothing.
 *
 * <p>This one refuses to skip. If the library is not loadable it FAILS, because
 * CI runs it immediately after building that library: "not loadable" there means
 * the build produced something the JVM cannot use, which is the single most
 * likely way this whole approach breaks.
 *
 * <h2>What it can and cannot check</h2>
 *
 * It cannot check that jaro-winkler is <i>correct</i> -- there is no oracle on
 * this side, and inventing one would mean writing the Java implementation this
 * arc exists to avoid. Cross-language equality against the Python/Rust scorer is
 * checked where an oracle exists: {@code test_spark_jvm_native_parity.py}.
 *
 * <p>What it checks here are the properties that hold for any correct kernel and
 * catch every marshaling bug: identity scores 1.0, alignment survives a batch,
 * multi-byte values are not truncated, absence is not evidence, and a bad
 * request is refused rather than scored.
 */
public final class NativeSelfTest {

  private static int failures = 0;

  public static void main(String[] args) {
    NativeScorer scorer = NativeScorer.createOrNull();
    System.out.println("  library: " + NativeLibrary.diagnostics());
    if (scorer == null) {
      System.err.println(
          "\nFAIL: the native library did not load. CI builds it in the step"
              + " before this one, so this means the build produced something the"
              + " JVM cannot use. Resource path sought: "
              + NativeLibrary.resourcePath());
      System.exit(1);
    }

    theJarSelectsTheNativeScorer();
    identicalStringsScoreOne(scorer);
    scoresStayAlignedAcrossABatch(scorer);
    multiByteValuesAreNotTruncated(scorer);
    absenceIsNotEvidence(scorer);
    everyScorerTheSparkTierUsesRuns(scorer);
    unknownScorerIdsAreRefused(scorer);
    mismatchedLengthsAreRefused(scorer);
    aLargeBatchSurvivesInOneCall(scorer);
    recordFingerprintsRunHereToo();

    if (failures > 0) {
      System.err.println("\n" + failures + " assertion(s) FAILED");
      System.exit(1);
    }
    System.out.println("\nall native self-tests passed");
  }

  /** The load-bearing one. Everything below could pass while the UDF Spark
   * actually calls had silently fallen back to ExactScorer. */
  static void theJarSelectsTheNativeScorer() {
    // ScorerSelection, not GoldenScoreUdf: the UDF only delegates here, and this
    // class is Spark-free so the assertion runs with no Spark classpath. The
    // delegation is what makes that equivalent -- there is one static, not two.
    String impl = ScorerSelection.implementationName();
    if ("NativeScorer".equals(impl)) {
      pass("the jar resolved NativeScorer");
    } else {
      fail("selected implementation", "want NativeScorer, got " + impl + " -- "
          + ScorerSelection.diagnostics());
    }
  }

  static void identicalStringsScoreOne(NativeScorer s) {
    // True of every scorer in the tier's set, and the cheapest possible check
    // that the buffers arrived intact: a marshaling bug makes a string unequal
    // to itself.
    String[] v = {"jonathan", "smith", "acme corporation"};
    for (int id : new int[] {GoldenScorer.JARO_WINKLER, GoldenScorer.LEVENSHTEIN,
        GoldenScorer.TOKEN_SORT, GoldenScorer.EXACT}) {
      Double[] got = s.score(id, v, v);
      boolean ok = true;
      for (Double d : got) {
        ok = ok && d != null && d == 1.0d;
      }
      if (ok) {
        pass("scorer " + id + ": identical strings score 1.0");
      } else {
        fail("scorer " + id + " identity", Arrays.toString(got));
      }
    }
  }

  static void scoresStayAlignedAcrossABatch(NativeScorer s) {
    // The failure this whole batching design is most exposed to: a score coming
    // back attached to the wrong pair. Constructed so misalignment cannot hide
    // -- exact matches interleaved with certain non-matches, so any shift shows.
    String[] a = {"aa", "bb", "cc", "dd", "ee", "ff"};
    String[] b = {"aa", "zz", "cc", "zz", "ee", "zz"};
    Double[] got = s.score(GoldenScorer.EXACT, a, b);
    eq("batch alignment", Arrays.asList(1.0d, 0.0d, 1.0d, 0.0d, 1.0d, 0.0d),
        Arrays.asList(got));
  }

  static void multiByteValuesAreNotTruncated(NativeScorer s) {
    // Offsets are BYTE offsets. An encoder that writes char counts cuts "Zoë"
    // short and the value stops matching itself -- and every multi-byte value
    // AFTER it in the batch is misread too, because the offsets have drifted.
    String[] a = {"Zoë", "日本語", "plain", "café"};
    Double[] got = s.score(GoldenScorer.EXACT, a, a);
    eq("multi-byte identity", Arrays.asList(1.0d, 1.0d, 1.0d, 1.0d), Arrays.asList(got));

    // And a multi-byte value must not read as equal to its ASCII-folded form.
    Double[] differ = s.score(GoldenScorer.EXACT, new String[] {"café"},
        new String[] {"cafe"});
    eq("multi-byte inequality", Arrays.asList(0.0d), Arrays.asList(differ));
  }

  static void absenceIsNotEvidence(NativeScorer s) {
    // null-vs-null must be null, NOT 1.0. The kernel has no validity bitmap by
    // design, so this is entirely the host's presence mask doing its job -- and
    // the failure it prevents (records merging on a shared absence) is one this
    // tier already shipped once.
    Double[] got = s.score(GoldenScorer.JARO_WINKLER,
        new String[] {null, "smith", null, "a"},
        new String[] {null, null, "jones", "a"});
    eq("absence", Arrays.asList(null, null, null, 1.0d), Arrays.asList(got));
  }

  static void everyScorerTheSparkTierUsesRuns(NativeScorer s) {
    // J0 could run one scorer. The point of J2 is that the restriction is gone,
    // so assert the tier's whole set actually executes rather than assuming the
    // dispatch reaches them.
    String[] a = {"jonathan smith"};
    String[] b = {"smith jonathon"};
    for (int id : new int[] {GoldenScorer.JARO_WINKLER, GoldenScorer.LEVENSHTEIN,
        GoldenScorer.TOKEN_SORT, GoldenScorer.EXACT}) {
      Double[] got = s.score(id, a, b);
      if (got.length == 1 && got[0] != null && got[0] >= 0.0d && got[0] <= 1.0d) {
        pass("scorer " + id + " runs (" + got[0] + ")");
      } else {
        fail("scorer " + id + " runs", Arrays.toString(got));
      }
    }
    if (!s.supports(GoldenScorer.JARO_WINKLER)) {
      fail("supports jaro_winkler", "the kernel is loaded but says it cannot");
    } else {
      pass("supports() reports the kernel's own id range (" + s.scorerIds() + " ids)");
    }
  }

  static void unknownScorerIdsAreRefused(NativeScorer s) {
    // score_one's catch-all returns 0.0 for an id it does not know, which is a
    // confident wrong answer rather than an error. The host must not let one
    // through.
    for (int id : new int[] {-1, s.scorerIds(), s.scorerIds() + 100, 9999}) {
      try {
        s.score(id, new String[] {"a"}, new String[] {"a"});
        fail("refuse scorer " + id, "expected UnsupportedOperationException");
      } catch (UnsupportedOperationException e) {
        pass("scorer id " + id + " refused");
      }
    }
  }

  static void mismatchedLengthsAreRefused(NativeScorer s) {
    try {
      s.score(GoldenScorer.EXACT, new String[] {"a"}, new String[] {"a", "b"});
      fail("length mismatch", "expected IllegalArgumentException");
    } catch (IllegalArgumentException e) {
      pass("length mismatch refused");
    }
  }

  static void aLargeBatchSurvivesInOneCall(NativeScorer s) {
    // The batch size the tier actually uses (batched.DEFAULT_BATCH_SIZE). The
    // whole approach rests on one downcall carrying this many pairs, and the
    // arrays it pins are proportionally large; a size limit would surface here
    // rather than in an executor.
    final int n = 10_000;
    String[] a = new String[n];
    String[] b = new String[n];
    for (int i = 0; i < n; i++) {
      a[i] = "value-" + i;
      b[i] = (i % 3 == 0) ? a[i] : "other-" + i;
    }
    Double[] got = s.score(GoldenScorer.EXACT, a, b);
    int matches = 0;
    for (int i = 0; i < n; i++) {
      if (got[i] != null && got[i] == 1.0d) {
        matches++;
      }
    }
    int want = (n + 2) / 3;
    if (got.length == n && matches == want) {
      pass("10,000 pairs in one call (" + matches + " exact matches)");
    } else {
      fail("large batch", "length " + got.length + ", " + matches + " matches, want "
          + want);
    }
  }

  static void recordFingerprintsRunHereToo() {
    // The identity graph's kernel, in the same library. Pinned vectors rather
    // than a computed oracle -- there is no second implementation on this side
    // to compare against, and these exact values are pinned in
    // fingerprint-core's own tests AND verified against Python's
    // record_fingerprint, so agreeing with them is agreeing with both.
    //
    // On aarch64 this is the only thing that checks the fingerprint kernel at
    // all: SHA-256 has no architecture-specific paths in this build, but that
    // is a claim worth one assertion rather than an assumption.
    eqStr("empty record",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        NativeFingerprint.of("{}"));
    eqStr("a real record",
        "a052f98b58d306db41c5672d7f5c5950895db304d2b4c4008ce1b4f649500b72",
        NativeFingerprint.of(
            "{\"name\":\"jonathan smith\",\"city\":\"boston\",\"n\":42}"));
    // Field ORDER must not matter -- the kernel sorts. A fingerprint that
    // depended on column order would split identities across two DataFrames
    // holding the same data.
    eqStr("field order is irrelevant",
        NativeFingerprint.of("{\"a\":1,\"b\":2}"),
        NativeFingerprint.of("{\"b\":2,\"a\":1}"));
    // `__`-prefixed keys are dropped INSIDE the kernel, so both surfaces agree
    // on which fields count without either having to remember to strip them.
    eqStr("__ keys are dropped",
        NativeFingerprint.of("{\"a\":1}"),
        NativeFingerprint.of("{\"a\":1,\"__row_id__\":7}"));
    // Unreadable input is REFUSED, not hashed into something plausible.
    if (NativeFingerprint.of("not json") == null) {
      pass("invalid JSON refused");
    } else {
      fail("invalid JSON", "expected null, got a fingerprint");
    }
    if (NativeFingerprint.of(null) == null) {
      pass("null input yields null");
    } else {
      fail("null input", "expected null");
    }
  }

  static void eqStr(String what, String want, String got) {
    if (want != null && want.equals(got)) {
      pass(what);
    } else {
      fail(what, "want " + want + ", got " + got);
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

  private NativeSelfTest() {}
}
