package dev.goldensuite.spark;

/** {@code java -jar goldenmatch-spark.jar} -- what this jar is, and whether its
 * native kernel loads HERE.
 *
 * <h2>Why a jar meant for Spark has a main()</h2>
 *
 * Because the question an operator has before submitting anything is "will the
 * native kernel actually load on my executors", and every other way of asking
 * costs a cluster round-trip. The jar falls back to the {@code exact}-only
 * scorer when the library will not load, and it does so silently by design --
 * a distributed job must not die because one executor could not {@code dlopen}
 * a shared library. The cost of that choice is that a misconfigured deployment
 * looks exactly like a working one from the outside.
 *
 * <p>So: copy the jar to a box that resembles an executor, run it, and read the
 * answer directly. No Spark, no session, no submit. It resolves the scorer
 * through the same {@link ScorerSelection} an executor uses, so a PASS here is
 * the same PASS an executor would get.
 *
 * <p>It also prints the version, which is the other thing you cannot recover
 * from a file called {@code goldenmatch-spark.jar}.
 *
 * <p>Exit code is 0 when the native kernel resolved and 1 when it fell back, so
 * this is usable as a preflight check in a deployment script rather than
 * something a human has to read.
 */
public final class Main {

  private Main() {}

  public static void main(String[] args) {
    String impl = ScorerSelection.implementationName();
    boolean nativeOk = "NativeScorer".equals(impl);

    System.out.println("goldenmatch-spark " + JarVersion.version());
    System.out.println("  runtime:        " + ScorerSelection.runtimeInfo());
    System.out.println("  os.arch:        " + System.getProperty("os.arch", "?"));
    System.out.println("  scorer:         " + impl
        + (nativeOk ? "  (native kernel)" : "  (FALLBACK -- exact matching only)"));
    System.out.println("  library:        " + ScorerSelection.diagnostics());

    if (!nativeOk) {
      System.out.println();
      System.out.println("The native kernel did not load, so this jar would score with `exact`");
      System.out.println("only -- every other scorer would be refused, on every executor, without");
      System.out.println("failing the job. The diagnostics above say why. The usual cause is a");
      System.out.println("platform with no library in the jar: it carries linux-x86-64 and");
      System.out.println("linux-aarch64.");
      System.exit(1);
    }
  }
}
