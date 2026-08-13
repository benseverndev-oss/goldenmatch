package dev.goldensuite.spark;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Locale;

/** Finds and loads {@code libgoldenmatch_score_jni}, the Rust kernel's JNI door.
 *
 * <h2>Why extraction, and not {@code System.loadLibrary}</h2>
 *
 * {@code loadLibrary} searches {@code java.library.path}, which means somebody
 * has to have put a {@code .so} on every executor and set a JVM flag. That is
 * precisely the "install something on the cluster" cost this whole arc exists to
 * remove -- the Python tier's answer was to ship a packed virtualenv, and
 * replacing one deployment apparatus with another would be no progress at all.
 *
 * <p>So the library rides <b>inside the jar</b> as a resource and is extracted
 * to a temp file on first use. {@code spark.addArtifact(jar)} then delivers
 * everything: one file, nothing installed, no flags. {@code loadLibrary} is
 * still tried last, for a host that genuinely prefers to manage it.
 *
 * <h2>Resolution order</h2>
 *
 * <ol>
 *   <li>{@code goldenmatch.score.jni.lib} system property, or the
 *       {@code GOLDENMATCH_SCORE_JNI_LIB} environment variable -- an explicit
 *       path. This is what CI uses to test a freshly-built library before a jar
 *       exists, and what a customer with their own build uses.</li>
 *   <li>The jar resource for this platform (see {@link #resourcePath}).</li>
 *   <li>{@code System.loadLibrary}.</li>
 * </ol>
 *
 * <h2>Failure is recorded, never thrown</h2>
 *
 * A distributed run must not die because one executor could not load a library;
 * the caller falls back. But a silent fallback is how you ship a path that
 * "works" and is quietly slower or narrower than intended -- this repo has
 * already lost time to a published wheel silently taking a fallback branch. So
 * the reason is kept in {@link #diagnostics()} and surfaced through a UDF, and
 * the Spark lane asserts on it rather than trusting that native was reached.
 */
public final class NativeLibrary {

  private NativeLibrary() {}

  /** Base name, matching {@code [lib].name} in score-jni's Cargo.toml. */
  static final String BASE = "goldenmatch_score_jni";

  /** Explicit-path overrides, checked before the bundled resource. */
  static final String PROPERTY = "goldenmatch.score.jni.lib";
  static final String ENV = "GOLDENMATCH_SCORE_JNI_LIB";

  private static boolean attempted;
  private static boolean loaded;
  private static String diagnostics = "not attempted";

  /** Load the library if it is not already loaded. Idempotent, and never throws.
   *
   * @return true if the library is usable
   */
  public static synchronized boolean ensureLoaded() {
    if (attempted) {
      return loaded;
    }
    attempted = true;
    StringBuilder tried = new StringBuilder();

    String explicit = System.getProperty(PROPERTY);
    if (explicit == null || explicit.isEmpty()) {
      explicit = System.getenv(ENV);
    }
    if (explicit != null && !explicit.isEmpty()) {
      try {
        System.load(Path.of(explicit).toAbsolutePath().toString());
        loaded = true;
        diagnostics = "loaded from " + explicit + " (explicit override)";
        return true;
      } catch (Throwable t) {
        tried.append("explicit ").append(explicit).append(": ").append(brief(t)).append("; ");
      }
    }

    String resource = resourcePath();
    try {
      Path extracted = extract(resource);
      System.load(extracted.toString());
      loaded = true;
      diagnostics = "loaded from jar resource " + resource + " extracted to " + extracted;
      return true;
    } catch (Throwable t) {
      tried.append("resource ").append(resource).append(": ").append(brief(t)).append("; ");
    }

    try {
      System.loadLibrary(BASE);
      loaded = true;
      diagnostics = "loaded via java.library.path";
      return true;
    } catch (Throwable t) {
      tried.append("loadLibrary ").append(BASE).append(": ").append(brief(t));
    }

    loaded = false;
    diagnostics = "NOT LOADED -- " + tried;
    return false;
  }

  /** Why the library did or did not load. Never null; carries the whole chain of
   * attempts on failure so a cluster problem is diagnosable from one string in a
   * query result rather than from executor logs nobody collected. */
  public static synchronized String diagnostics() {
    return diagnostics;
  }

  /** Record that a library which LOADED cannot be used -- an ABI skew, or
   * symbols that are not the ones this jar expects.
   *
   * <p>A separate state from "did not load" on purpose. The two produce the same
   * fallback but have completely different fixes (ship the library vs. rebuild
   * the pair together), and a diagnostic that conflates them sends whoever reads
   * it looking in the wrong place.
   */
  static synchronized void recordUnusable(String reason) {
    loaded = false;
    diagnostics = "LOADED BUT UNUSABLE -- " + reason;
  }

  /** The classpath resource holding the library for the running platform, e.g.
   * {@code /native/linux-x86-64/libgoldenmatch_score_jni.so}.
   *
   * <p>Package-private and deterministic so a test can assert the mapping
   * without a library present. The jar built in CI carries linux-x86-64 only,
   * because that is what a Spark executor runs; the other names resolve so that
   * adding an arch is a build change and not a code change.
   */
  static String resourcePath() {
    return "/native/" + platform() + "/" + fileName();
  }

  /** {@code os-arch}, in the naming this project's jar layout uses. */
  static String platform() {
    String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
    String arch = System.getProperty("os.arch", "").toLowerCase(Locale.ROOT);

    String osKey;
    if (os.contains("linux")) {
      osKey = "linux";
    } else if (os.contains("mac") || os.contains("darwin")) {
      osKey = "darwin";
    } else if (os.contains("win")) {
      osKey = "windows";
    } else {
      osKey = "unknown";
    }

    String archKey;
    if (arch.equals("amd64") || arch.equals("x86_64")) {
      archKey = "x86-64";
    } else if (arch.equals("aarch64") || arch.equals("arm64")) {
      archKey = "aarch64";
    } else {
      archKey = arch.isEmpty() ? "unknown" : arch;
    }
    return osKey + "-" + archKey;
  }

  /** Platform library file name for {@link #BASE}. */
  static String fileName() {
    String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
    if (os.contains("win")) {
      return BASE + ".dll";
    }
    if (os.contains("mac") || os.contains("darwin")) {
      return "lib" + BASE + ".dylib";
    }
    return "lib" + BASE + ".so";
  }

  /** Copy a classpath resource to a temp file and return its path.
   *
   * <p>{@code System.load} needs a real filesystem path -- it cannot read out of
   * a jar. The file is marked delete-on-exit rather than deleted eagerly: a
   * loaded library must stay on disk for the life of the JVM, and an executor
   * JVM outlives any single task.
   */
  private static Path extract(String resource) throws IOException {
    try (InputStream in = NativeLibrary.class.getResourceAsStream(resource)) {
      if (in == null) {
        throw new IOException("not on the classpath");
      }
      Path dir = Files.createTempDirectory("goldenmatch-jni-");
      Path target = dir.resolve(fileName());
      try (OutputStream out = Files.newOutputStream(target)) {
        in.transferTo(out);
      }
      target.toFile().deleteOnExit();
      dir.toFile().deleteOnExit();
      return target;
    }
  }

  private static String brief(Throwable t) {
    String m = t.getMessage();
    return t.getClass().getSimpleName() + (m == null ? "" : ": " + m);
  }
}
