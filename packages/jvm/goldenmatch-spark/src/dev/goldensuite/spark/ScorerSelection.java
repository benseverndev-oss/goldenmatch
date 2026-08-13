package dev.goldensuite.spark;

/** Decides which {@link GoldenScorer} this JVM runs, once.
 *
 * <h2>Why this is its own class</h2>
 *
 * It is Spark-free, and the decision it makes is the one most worth testing:
 * whether the native kernel was reached or the jar quietly fell back. Leaving it
 * inside {@link GoldenScoreUdf} would put it behind a Spark classpath, so the
 * only way to test it would be to spin up a session -- and the piece that must
 * never go untested would be the piece hardest to test. Every other class here
 * with a decision in it ({@link SeqCoercion}, {@link Utf8Batch},
 * {@link NativeScorer}) is Spark-free for the same reason.
 *
 * <h2>Resolved once, per JVM</h2>
 *
 * A {@code static} because the alternative is re-attempting a filesystem
 * extraction and a {@code dlopen} on every batch. On a Spark cluster that means
 * once per executor JVM, which is exactly the granularity that matters: the
 * driver loading the library successfully says nothing about whether an executor
 * can.
 *
 * <h2>The fallback is deliberate, and it announces itself</h2>
 *
 * When the native library will not load, this returns {@link ExactScorer} rather
 * than throwing. A distributed job must not die because one executor could not
 * load a shared library -- correctness and availability first.
 *
 * <p>But a fallback nobody can see is worse than no fallback: the query still
 * returns numbers, from a narrower path, and every version string still looks
 * right. This repo has already lost real time to that exact shape. So
 * {@link #implementationName()} and {@link #diagnostics()} are queryable through
 * {@link GoldenScoreImplUdf}, and the Spark lane asserts on them.
 */
public final class ScorerSelection {

  private ScorerSelection() {}

  private static final GoldenScorer SCORER = select();

  private static GoldenScorer select() {
    GoldenScorer kernel = NativeScorer.createOrNull();
    return kernel != null ? kernel : new ExactScorer();
  }

  /** The scorer this JVM uses. Never null. */
  public static GoldenScorer scorer() {
    return SCORER;
  }

  /** Simple class name of the scorer in use -- {@code NativeScorer} when the
   * kernel loaded, {@code ExactScorer} when it did not. */
  public static String implementationName() {
    return SCORER.getClass().getSimpleName();
  }

  /** Why the native library did or did not load, verbatim. */
  public static String diagnostics() {
    return NativeLibrary.diagnostics();
  }

  /** The JVM this scorer is running in: version, heap ceiling, CPU count.
   *
   * <h2>Why a scorer reports heap</h2>
   *
   * Because nothing else can, and the number decides how results are read. The
   * batched path materialises each group as an array in JVM heap, so its memory
   * ceiling is a property of the executor, not of the algorithm -- and a
   * benchmark that OOMs at an unknown heap size has measured a configuration
   * rather than a design.
   *
   * <p>That is not hypothetical here: the batched arm of this arc's benchmark
   * died with {@code java.lang.OutOfMemoryError: Java heap space} while the
   * row-shaped arm completed the same workload on the same box, and nobody knew
   * what heap either had. A Spark Connect client cannot ask -- there is no
   * {@code SparkContext} to interrogate -- but a UDF is already running in the
   * JVM that has the answer.
   *
   * <p>{@code maxMemory()} is the ceiling the JVM will grow to (i.e. {@code -Xmx}),
   * which is the number that matters; {@code totalMemory()} is only what it has
   * committed so far and would read as a much smaller, misleading limit.
   */
  public static String runtimeInfo() {
    Runtime rt = Runtime.getRuntime();
    long maxMb = rt.maxMemory() / (1024L * 1024L);
    return "java=" + System.getProperty("java.version", "?")
        + " heap_max=" + maxMb + "MB"
        + " cpus=" + rt.availableProcessors();
  }
}
